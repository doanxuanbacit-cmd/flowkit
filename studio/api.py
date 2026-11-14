"""Studio V4 HTTP API, mounted into FlowKit's FastAPI app under /api/studio.

Same trust model as FlowKit (local-only, cross-site requests refused by the
FlowKit middleware). File access is restricted to Studio's data dir and
FlowKit's output dir.
"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from studio import assets, config, db, flowkit_bridge, jobs
from studio.editing import ffmpeg
from studio.editing.engine import RenderOptions, to_edl
from studio.editing.schemas import Timeline
from studio.editing.skills import REGISTRY
from studio.publish import service as pub
from studio.publish.base import PublishError
from studio.publish.crypto import CryptoError

router = APIRouter(prefix="/api/studio", tags=["studio"])
ANALYSIS_SKILLS = {"edit-smart-cut", "edit-dialogue-sync", "edit-beat-sync", "edit-multi-format", "edit-master-qa",
                   "edit-director", "edit-color-grading", "edit-continuity"}
_render_worker: jobs.RenderWorker | None = None
_publish_worker: pub.PublishWorker | None = None


def _allowed(path: str | Path) -> Path:
    p = Path(path).resolve()
    roots = [config.STUDIO_DIR.resolve(), config.OUTPUT_DIR.resolve()]
    if not any(p == r or r in p.parents for r in roots):
        raise HTTPException(403, "path outside Studio/FlowKit media folders")
    if not p.exists():
        raise HTTPException(404, "file not found")
    return p


def start_background(loop: asyncio.AbstractEventLoop | None = None) -> None:
    global _render_worker, _publish_worker
    config.ensure_dirs()
    db.migrate()
    if loop:
        jobs.bind_loop(loop)
    _render_worker = jobs.RenderWorker()
    _render_worker.start()
    _publish_worker = pub.PublishWorker()
    _publish_worker.start()


def stop_background() -> None:
    if _render_worker:
        _render_worker.stop()
    if _publish_worker:
        _publish_worker.stop()


# ---------------------------------------------------------------- system
@router.get("/health")
def health():
    v = ffmpeg.run(["-version"], check=False).stdout.splitlines()[:1]
    with db.connect() as conn:
        ver = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    return {"ok": True, "ffmpeg": v[0] if v else None, "db_version": ver, "skills": len(REGISTRY),
            "approval_required": config.REQUIRE_APPROVAL, "secret_key_set": bool(config.SECRET_KEY),
            "workers": {"render": bool(_render_worker), "publish": bool(_publish_worker)}}


@router.get("/skills")
def skills():
    return [{"name": s.name, "slot": s.slot, "summary": s.summary, "runnable": s.name in ANALYSIS_SKILLS}
            for s in REGISTRY.values()]


@router.get("/skills/{name}/schema")
def skill_schema(name: str):
    if name not in REGISTRY:
        raise HTTPException(404, "unknown skill")
    return REGISTRY[name].schema()


def _check_paths(obj) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and k in ("path", "source", "music", "clip_audio", "reference", "premaster", "audio"):
                _allowed(v)
            else:
                _check_paths(v)
    elif isinstance(obj, list):
        for v in obj:
            _check_paths(v)


@router.post("/skills/{name}/run")
async def run_skill(name: str, payload: dict):
    if name not in ANALYSIS_SKILLS:
        raise HTTPException(400, "skill is not runnable standalone (it runs inside a render)")
    _check_paths(payload)
    try:
        out = await asyncio.to_thread(REGISTRY[name], payload)
    except (ValueError, ffmpeg.FFmpegError) as e:
        raise HTTPException(422, str(e)[:500])
    return out.model_dump(mode="json")


# ---------------------------------------------------------------- assets
@router.get("/assets")
def list_assets(kind: Optional[str] = None, limit: int = Query(200, le=1000), offset: int = 0):
    return assets.list_assets(kind, limit, offset)


@router.post("/assets/upload")
async def upload_asset(file: UploadFile = File(...)):
    suffix = Path(file.filename or "upload.bin").suffix.lower()
    if suffix not in assets.KIND_BY_EXT:
        raise HTTPException(415, f"unsupported file type {suffix}")
    config.ensure_dirs()
    limit = config.MAX_UPLOAD_MB * 1024 * 1024
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=config.STUDIO_DIR) as tmp:
        size = 0
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > limit:
                tmp.close()
                Path(tmp.name).unlink(missing_ok=True)
                raise HTTPException(413, "file too large")
            tmp.write(chunk)
    try:
        return await asyncio.to_thread(assets.import_file, tmp.name, original_name=file.filename, move=True)
    except ValueError as e:
        Path(tmp.name).unlink(missing_ok=True)
        raise HTTPException(422, str(e))


class ImportPath(BaseModel):
    path: str


@router.post("/assets/import-path")
async def import_path(body: ImportPath):
    p = _allowed(body.path)
    return await asyncio.to_thread(assets.import_file, p, source="import")


@router.get("/assets/{aid}/file")
def asset_file(aid: str):
    a = assets.get(aid)
    if not a:
        raise HTTPException(404, "asset not found")
    return FileResponse(_allowed(a["path"]), filename=a["original_name"])


@router.delete("/assets/{aid}")
def delete_asset(aid: str):
    if not assets.delete(aid):
        raise HTTPException(404, "asset not found")
    return {"deleted": aid}


# ---------------------------------------------------------------- FlowKit
@router.get("/flowkit/videos")
def flowkit_videos():
    try:
        return flowkit_bridge.list_videos()
    except flowkit_bridge.BridgeError as e:
        raise HTTPException(404, str(e))


class FlowImport(BaseModel):
    video_id: str
    orientation: str = "VERTICAL"
    save_timeline: bool = True


@router.post("/flowkit/import")
async def flowkit_import(body: FlowImport):
    try:
        res = await asyncio.to_thread(flowkit_bridge.import_video, body.video_id, body.orientation)
    except flowkit_bridge.BridgeError as e:
        raise HTTPException(400, str(e))
    if body.save_timeline:
        jobs.save_timeline(Timeline.model_validate(res["timeline"]), flowkit_video_id=body.video_id)
    return res


# ---------------------------------------------------------------- timelines
@router.get("/timelines")
def timelines():
    return jobs.list_timelines()


@router.get("/timelines/{tid}")
def timeline(tid: str):
    t = jobs.get_timeline(tid)
    if not t:
        raise HTTPException(404, "timeline not found")
    return t


@router.put("/timelines/{tid}")
def put_timeline(tid: str, tl: Timeline):
    if tl.id != tid:
        raise HTTPException(400, "id mismatch")
    for c in tl.clips:
        _allowed(c.source)
    return jobs.save_timeline(tl)


@router.delete("/timelines/{tid}")
def del_timeline(tid: str):
    if not jobs.delete_timeline(tid):
        raise HTTPException(404, "timeline not found")
    return {"deleted": tid}


@router.get("/timelines/{tid}/edl", response_class=PlainTextResponse)
def timeline_edl(tid: str):
    t = jobs.get_timeline(tid)
    if not t:
        raise HTTPException(404, "timeline not found")
    return to_edl(Timeline.model_validate(t["data"]))


# ---------------------------------------------------------------- renders
@router.post("/timelines/{tid}/render")
def render(tid: str, options: RenderOptions | None = None):
    try:
        return jobs.enqueue_render(tid, options)
    except KeyError:
        raise HTTPException(404, "timeline not found")


@router.get("/renders")
def renders(status: Optional[str] = None, limit: int = Query(100, le=500)):
    return jobs.list_jobs(status, limit)


@router.get("/renders/{jid}")
def render_job(jid: str):
    j = jobs.get_job(jid)
    if not j:
        raise HTTPException(404, "render not found")
    return j


@router.post("/renders/{jid}/cancel")
def cancel_render(jid: str):
    if not jobs.cancel_job(jid):
        raise HTTPException(409, "only queued renders can be cancelled")
    return {"cancelled": jid}


@router.get("/renders/{jid}/qa")
def render_qa(jid: str):
    j = jobs.get_job(jid)
    if not j:
        raise HTTPException(404, "render not found")
    return jobs.qa_report(j) or {}


@router.get("/renders/{jid}/files/{name}")
def render_file(jid: str, name: str):
    j = jobs.get_job(jid)
    if not j:
        raise HTTPException(404, "render not found")
    f = jobs.result_files(j).get(name)
    if not f:
        raise HTTPException(404, "file not found")
    return FileResponse(_allowed(f), filename=f.name)


class Decision(BaseModel):
    approve: bool
    reviewer: str = Field(min_length=1, max_length=80)
    note: str = ""


@router.post("/renders/{jid}/review")
def review(jid: str, d: Decision):
    try:
        return jobs.decide(jid, d.approve, d.reviewer, d.note)
    except KeyError:
        raise HTTPException(404, "render not found")
    except ValueError as e:
        raise HTTPException(409, str(e))


# ---------------------------------------------------------------- social accounts & publishing
class ConnectAccount(BaseModel):
    platform: str
    name: str
    credentials: dict
    external_id: Optional[str] = None
    verify: bool = True


@router.get("/accounts")
def accounts():
    return pub.list_accounts()


@router.post("/accounts")
async def connect(body: ConnectAccount):
    try:
        return await asyncio.to_thread(pub.connect_account, body.platform, body.name, body.credentials,
                                       body.external_id, body.verify)
    except CryptoError as e:
        raise HTTPException(500, str(e))
    except (ValueError, PublishError, KeyError) as e:
        raise HTTPException(400, f"could not verify account: {str(e)[:300]}")


@router.post("/accounts/{aid}/disable")
def disable(aid: str):
    pub.disable_account(aid)
    return pub.get_account(aid)


class PublishReq(BaseModel):
    render_job_id: str
    account_id: str
    title: str = ""
    caption: str = ""
    tags: list[str] = []
    privacy: str = "private"
    scheduled_at: Optional[str] = None
    rendition: Optional[str] = None
    options: dict = {}
    idempotency_key: Optional[str] = None


@router.post("/publish")
def create_publish(body: PublishReq):
    try:
        return pub.create_publish_job(**body.model_dump())
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.get("/publish")
def publish_jobs(status: Optional[str] = None):
    return pub.list_publish_jobs(status)


@router.get("/publish/calendar")
def publish_calendar(month: str = Query(..., pattern=r"^\d{4}-\d{2}$")):
    return pub.calendar(month)


@router.post("/publish/{jid}/cancel")
def publish_cancel(jid: str):
    if not pub.cancel(jid):
        raise HTTPException(409, "job cannot be cancelled in its current state")
    return pub.get_publish_job(jid)


@router.post("/publish/{jid}/retry")
def publish_retry(jid: str):
    try:
        return pub.retry_now(jid)
    except ValueError as e:
        raise HTTPException(409, str(e))


class ManualMark(BaseModel):
    permalink: str
    external_id: Optional[str] = None
    actor: str = "user"


@router.post("/publish/{jid}/mark-published")
def publish_mark(jid: str, body: ManualMark):
    if not pub.get_publish_job(jid):
        raise HTTPException(404, "job not found")
    return pub.mark_published(jid, body.permalink, body.external_id, body.actor)


# ---------------------------------------------------------------- audit & analytics
@router.get("/audit")
def audit(entity: Optional[str] = None, entity_id: Optional[str] = None, limit: int = Query(200, le=2000)):
    q, args = "SELECT * FROM audit_log WHERE 1=1", []
    if entity:
        q += " AND entity=?"
        args.append(entity)
    if entity_id:
        q += " AND entity_id=?"
        args.append(entity_id)
    with db.connect() as conn:
        return [db.row(r, ("detail",)) for r in conn.execute(q + " ORDER BY id DESC LIMIT ?", (*args, limit))]


@router.get("/overview")
def overview():
    with db.connect() as conn:
        def count(sql):
            return {r[0]: r[1] for r in conn.execute(sql)}
        return {
            "renders": count("SELECT status, COUNT(*) FROM render_job GROUP BY status"),
            "approvals": count("SELECT approval, COUNT(*) FROM render_job WHERE status='COMPLETED' GROUP BY approval"),
            "publish": count("SELECT status, COUNT(*) FROM publish_job GROUP BY status"),
            "assets": count("SELECT kind, COUNT(*) FROM asset GROUP BY kind"),
            "timelines": conn.execute("SELECT COUNT(*) FROM timeline").fetchone()[0],
            "accounts": count("SELECT platform, COUNT(*) FROM social_account WHERE status='ACTIVE' GROUP BY platform"),
        }


@router.get("/analytics")
def analytics():
    """Only metrics actually fetched from platform APIs; nothing is estimated."""
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT p.id, p.platform, p.title, p.permalink, m.fetched_at, m.views, m.likes, m.comments, m.shares "
            "FROM publish_job p JOIN metric_snapshot m ON m.id = (SELECT id FROM metric_snapshot "
            "WHERE publish_job_id=p.id ORDER BY fetched_at DESC LIMIT 1) ORDER BY m.fetched_at DESC")]
    return {"items": rows, "note": None if rows else "no metrics collected yet (analytics connectors are P3)"}


def remove_render_outputs(jid: str) -> None:
    shutil.rmtree(config.RENDERS_DIR / jid, ignore_errors=True)
