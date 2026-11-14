"""Timelines (versioned EDLs), render jobs, approval gate and the render worker.

Worker = local thread pool polling the render_job table (claim is an atomic
UPDATE, so several workers/processes never take the same job). Swap for Redis/RQ
later without changing the API: only `RenderWorker` touches the queue.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from pathlib import Path

from studio import config, db
from studio.editing.engine import Engine, RenderOptions
from studio.editing.schemas import Timeline

logger = logging.getLogger(__name__)
_loop: asyncio.AbstractEventLoop | None = None


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def emit(kind: str, data: dict) -> None:
    """Forward a realtime event to FlowKit's dashboard WebSocket bus."""
    if _loop is None:
        return
    try:
        from agent.services.event_bus import event_bus
        asyncio.run_coroutine_threadsafe(event_bus.emit(kind, data), _loop)
    except Exception:  # noqa: BLE001 - events are best effort
        pass


# ------------------------------------------------------------------ timelines
def save_timeline(tl: Timeline, flowkit_video_id: str | None = None, actor: str = "user") -> dict:
    data = tl.model_dump_json()
    with db.connect() as conn:
        cur = conn.execute("SELECT version FROM timeline WHERE id=?", (tl.id,)).fetchone()
        if cur:
            conn.execute("UPDATE timeline SET title=?, data=?, version=version+1, updated_at=? WHERE id=?",
                         (tl.title, data, db.now(), tl.id))
        else:
            conn.execute("INSERT INTO timeline(id,title,flowkit_video_id,data,version,created_at,updated_at) "
                         "VALUES (?,?,?,?,1,?,?)", (tl.id, tl.title, flowkit_video_id, data, db.now(), db.now()))
        db.audit(actor, "timeline.save", "timeline", tl.id, {"clips": len(tl.clips)}, conn)
    return get_timeline(tl.id)


def get_timeline(tid: str) -> dict | None:
    with db.connect() as conn:
        return db.row(conn.execute("SELECT * FROM timeline WHERE id=?", (tid,)).fetchone(), ("data",))


def list_timelines() -> list[dict]:
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id,title,flowkit_video_id,version,created_at,updated_at FROM timeline ORDER BY updated_at DESC")]


def delete_timeline(tid: str) -> bool:
    with db.connect() as conn:
        n = conn.execute("DELETE FROM timeline WHERE id=?", (tid,)).rowcount
        if n:
            db.audit("user", "timeline.delete", "timeline", tid, {}, conn)
        return bool(n)


# ------------------------------------------------------------------ render jobs
JOB_JSON = ("options", "result")


def enqueue_render(tid: str, options: RenderOptions | None = None, actor: str = "user") -> dict:
    tl = get_timeline(tid)
    if not tl:
        raise KeyError(tid)
    jid = db.new_id()
    with db.connect() as conn:
        conn.execute("INSERT INTO render_job(id,timeline_id,timeline_version,options,created_at) VALUES (?,?,?,?,?)",
                     (jid, tid, tl["version"], (options or RenderOptions()).model_dump_json(), db.now()))
        db.audit(actor, "render.enqueue", "render_job", jid, {"timeline": tid}, conn)
    emit("studio_render", {"id": jid, "status": "QUEUED"})
    return get_job(jid)


def get_job(jid: str) -> dict | None:
    with db.connect() as conn:
        return db.row(conn.execute("SELECT * FROM render_job WHERE id=?", (jid,)).fetchone(), JOB_JSON)


def list_jobs(status: str | None = None, limit: int = 100) -> list[dict]:
    with db.connect() as conn:
        q = "SELECT * FROM render_job" + (" WHERE status=?" if status else "") + " ORDER BY created_at DESC LIMIT ?"
        args = (status, limit) if status else (limit,)
        return [db.row(r, JOB_JSON) for r in conn.execute(q, args)]


def cancel_job(jid: str, actor: str = "user") -> bool:
    with db.connect() as conn:
        n = conn.execute("UPDATE render_job SET status='CANCELLED', finished_at=? WHERE id=? AND status='QUEUED'",
                         (db.now(), jid)).rowcount
        if n:
            db.audit(actor, "render.cancel", "render_job", jid, {}, conn)
        return bool(n)


def decide(jid: str, approve: bool, reviewer: str, note: str = "") -> dict:
    """Approval gate. A render that failed QA can only be approved with a note
    (explicit manual override is recorded in the audit log)."""
    job = get_job(jid)
    if not job:
        raise KeyError(jid)
    if job["status"] != "COMPLETED":
        raise ValueError("only completed renders can be reviewed")
    if approve and not job["qa_passed"] and not note.strip():
        raise ValueError("render failed QA: approving needs an override note")
    with db.connect() as conn:
        conn.execute("UPDATE render_job SET approval=?, approved_by=?, approval_note=?, approved_at=? WHERE id=?",
                     ("APPROVED" if approve else "REJECTED", reviewer, note, db.now(), jid))
        db.audit(reviewer, "render.approve" if approve else "render.reject", "render_job", jid,
                 {"qa_passed": bool(job["qa_passed"]), "override": approve and not job["qa_passed"], "note": note}, conn)
        if approve:
            conn.execute("UPDATE publish_job SET status=CASE WHEN scheduled_at IS NOT NULL THEN 'SCHEDULED' "
                         "ELSE 'QUEUED' END, updated_at=? WHERE render_job_id=? AND status='WAITING_APPROVAL'",
                         (db.now(), jid))
        else:
            conn.execute("UPDATE publish_job SET status='CANCELLED', error='render rejected', updated_at=? "
                         "WHERE render_job_id=? AND status='WAITING_APPROVAL'", (db.now(), jid))
    emit("studio_render", {"id": jid, "approval": "APPROVED" if approve else "REJECTED"})
    return get_job(jid)


def _claim() -> dict | None:
    with db.connect() as conn:
        r = conn.execute("SELECT id FROM render_job WHERE status='QUEUED' ORDER BY created_at LIMIT 1").fetchone()
        if not r:
            return None
        n = conn.execute("UPDATE render_job SET status='RUNNING', started_at=?, attempts=attempts+1 "
                         "WHERE id=? AND status='QUEUED'", (db.now(), r["id"])).rowcount
        return get_job(r["id"]) if n else None


def run_job(job: dict) -> dict:
    jid = job["id"]
    tl_row = get_timeline(job["timeline_id"])
    last = {"t": 0.0}

    def progress(stage: str, pct: float) -> None:
        if time.time() - last["t"] < 0.5 and stage != "done":
            return
        last["t"] = time.time()
        with db.connect() as conn:
            conn.execute("UPDATE render_job SET stage=?, progress=? WHERE id=?", (stage, round(pct, 3), jid))
        emit("studio_render", {"id": jid, "status": "RUNNING", "stage": stage, "progress": pct})

    try:
        tl = Timeline.model_validate(tl_row["data"])
        opts = RenderOptions.model_validate(job.get("options") or {})
        out = config.RENDERS_DIR / jid
        res = Engine(config.CACHE_DIR, progress).render(tl, out, opts)
        with db.connect() as conn:
            conn.execute("UPDATE render_job SET status='COMPLETED', result=?, qa_passed=?, progress=1, stage='done', "
                         "finished_at=? WHERE id=?", (res.model_dump_json(), int(res.qa_passed), db.now(), jid))
            db.audit("worker", "render.complete", "render_job", jid,
                     {"qa_passed": res.qa_passed, "duration": res.duration}, conn)
        emit("studio_render", {"id": jid, "status": "COMPLETED", "qa_passed": res.qa_passed})
    except Exception as e:  # noqa: BLE001 - job boundary
        logger.exception("render %s failed", jid)
        with db.connect() as conn:
            conn.execute("UPDATE render_job SET status='FAILED', error=?, finished_at=? WHERE id=?",
                         (str(e)[:2000], db.now(), jid))
            db.audit("worker", "render.fail", "render_job", jid, {"error": str(e)[:300]}, conn)
        emit("studio_render", {"id": jid, "status": "FAILED"})
    return get_job(jid)


class RenderWorker:
    def __init__(self, workers: int = config.RENDER_WORKERS, poll_s: float = 1.0):
        self.workers = workers
        self.poll_s = poll_s
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def _loop(self) -> None:
        while not self._stop.is_set():
            job = _claim()
            if job:
                run_job(job)
            else:
                self._stop.wait(self.poll_s)

    def start(self) -> None:
        # Jobs left RUNNING by a crash go back to the queue once.
        with db.connect() as conn:
            conn.execute("UPDATE render_job SET status=CASE WHEN attempts<3 THEN 'QUEUED' ELSE 'FAILED' END, "
                         "error=CASE WHEN attempts<3 THEN error ELSE 'worker crashed 3 times' END "
                         "WHERE status='RUNNING'")
        for i in range(self.workers):
            t = threading.Thread(target=self._loop, name=f"studio-render-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self, timeout: float = 5) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout)


def result_files(job: dict) -> dict[str, Path]:
    """Deliverables of a completed render, for download/publish."""
    res = job.get("result") or {}
    files = {"master": res.get("master"), "thumbnail": res.get("thumbnail"), "srt": res.get("srt"),
             "metadata": res.get("metadata"), "manifest": res.get("manifest"), "qa_report": res.get("qa_report"),
             "continuity_sheet": str(Path(res.get("out_dir", "")) / "continuity_sheet.jpg") if res else None}
    for r in res.get("renditions", []):
        files[r["platform"]] = r["path"]
    return {k: Path(v) for k, v in files.items() if v and Path(v).exists()}


def qa_report(job: dict) -> dict | None:
    f = result_files(job).get("qa_report")
    return json.loads(f.read_text(encoding="utf-8")) if f else None
