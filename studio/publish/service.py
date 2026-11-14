"""Publishing service: accounts, approval-gated queue, worker, manual override.

Guarantees
- Idempotency: one publish_job per (render, account, rendition) unless the
  caller passes its own key; duplicates return the existing job.
- Approval gate: nothing leaves WAITING_APPROVAL until the render is APPROVED
  (QA-failed renders need an explicit override note — see jobs.decide).
- Per-platform queue: at most one in-flight upload per platform, with a
  minimum interval between posts (rate-limit friendly).
- Retries: exponential backoff with jitter, capped attempts, reason recorded.
- Resumable: publisher state checkpoints persist after every step.
- Manual fallback: MANUAL_REQUIRED jobs get a ready-to-post package folder.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from studio import config, db, jobs
from studio.publish.base import (
    AuthError, ManualRequired, PermanentError, PublishContext, Publisher, RetryableError,
)
from studio.publish.crypto import decrypt_json, encrypt_json
from studio.publish.facebook import FacebookReelsPublisher
from studio.publish.tiktok import TikTokPublisher
from studio.publish.youtube import YouTubeShortsPublisher
from studio.publish.zalo import ZaloOAPublisher

logger = logging.getLogger(__name__)
PUBLISHERS: dict[str, type[Publisher]] = {
    "facebook": FacebookReelsPublisher, "youtube": YouTubeShortsPublisher,
    "tiktok": TikTokPublisher, "zalo": ZaloOAPublisher,
}
DEFAULT_RENDITION = {"facebook": "facebook_reels", "youtube": "youtube_shorts", "tiktok": "tiktok", "zalo": "zalo"}
MIN_INTERVAL_S = {"facebook": 60, "youtube": 60, "tiktok": 120, "zalo": 60}
ACC_JSON = ("scopes", "capabilities")
PUB_JSON = ("tags", "options", "state", "manual_package")
_factory: dict[str, Publisher] = {}


def set_publisher(platform: str, pub: Publisher) -> None:
    """Inject a publisher (tests / custom HTTP client)."""
    _factory[platform] = pub


def publisher(platform: str) -> Publisher:
    if platform not in _factory:
        _factory[platform] = PUBLISHERS[platform]()
    return _factory[platform]


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def notify(event: str, data: dict) -> None:
    jobs.emit(f"studio_{event}", data)
    url = os.environ.get("STUDIO_NOTIFY_WEBHOOK")
    if url:
        try:
            httpx.post(url, json={"event": event, **data}, timeout=10)
        except httpx.HTTPError:
            logger.warning("notify webhook failed for %s", event)


# ---------------------------------------------------------------- accounts
def connect_account(platform: str, name: str, credentials: dict, external_id: str | None = None,
                    verify: bool = True, actor: str = "user") -> dict:
    if platform not in PUBLISHERS:
        raise ValueError(f"unsupported platform {platform}")
    caps: dict = {}
    if verify:
        caps = publisher(platform).capabilities(credentials, {})
    aid = db.new_id()
    with db.connect() as conn:
        conn.execute("INSERT INTO social_account(id,platform,name,external_id,credentials_enc,scopes,capabilities,"
                     "status,created_at,updated_at) VALUES (?,?,?,?,?,?,?, 'ACTIVE',?,?)",
                     (aid, platform, name, external_id, encrypt_json(credentials),
                      db.dumps(sorted(set((credentials.get("scope") or "").replace(" ", "").split(",")) - {""})),
                      db.dumps(caps), db.now(), db.now()))
        db.audit(actor, "account.connect", "social_account", aid, {"platform": platform, "capabilities": caps}, conn)
    return get_account(aid)


def get_account(aid: str) -> dict | None:
    with db.connect() as conn:
        a = db.row(conn.execute("SELECT * FROM social_account WHERE id=?", (aid,)).fetchone(), ACC_JSON)
    if a:
        a.pop("credentials_enc", None)
    return a


def list_accounts() -> list[dict]:
    with db.connect() as conn:
        rows = [db.row(r, ACC_JSON) for r in conn.execute("SELECT * FROM social_account ORDER BY created_at")]
    for a in rows:
        a.pop("credentials_enc", None)
    return rows


def _credentials(aid: str) -> dict:
    with db.connect() as conn:
        r = conn.execute("SELECT credentials_enc FROM social_account WHERE id=?", (aid,)).fetchone()
    return decrypt_json(r["credentials_enc"])


def _save_credentials(aid: str, creds: dict) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE social_account SET credentials_enc=?, status='ACTIVE', updated_at=? WHERE id=?",
                     (encrypt_json(creds), db.now(), aid))
        db.audit("system", "account.token_refresh", "social_account", aid, {}, conn)


def disable_account(aid: str, actor: str = "user") -> None:
    with db.connect() as conn:
        conn.execute("UPDATE social_account SET status='DISABLED', updated_at=? WHERE id=?", (db.now(), aid))
        db.audit(actor, "account.disable", "social_account", aid, {}, conn)


# ---------------------------------------------------------------- jobs
def cleared(render: dict) -> bool:
    """May this render be published? Approved, or (approval off) QA passed."""
    if render["approval"] == "APPROVED":
        return True
    return not config.REQUIRE_APPROVAL and bool(render["qa_passed"]) and render["approval"] != "REJECTED"


def create_publish_job(render_job_id: str, account_id: str, *, title: str = "", caption: str = "",
                       tags: list[str] | None = None, privacy: str = "private", scheduled_at: str | None = None,
                       rendition: str | None = None, options: dict | None = None, idempotency_key: str | None = None,
                       actor: str = "user") -> dict:
    render = jobs.get_job(render_job_id)
    acc = get_account(account_id)
    if not render or not acc:
        raise KeyError("render or account not found")
    if render["status"] != "COMPLETED":
        raise ValueError("render is not completed")
    if render["approval"] == "REJECTED":
        raise ValueError("render was rejected")
    if acc["status"] != "ACTIVE":
        raise ValueError(f"account is {acc['status']}")
    plat = acc["platform"]
    rendition = rendition or DEFAULT_RENDITION[plat]
    files = jobs.result_files(render)
    if rendition not in files:
        raise ValueError(f"render has no '{rendition}' rendition (available: {sorted(files)})")
    key = idempotency_key or hashlib.sha256(f"{render_job_id}|{account_id}|{rendition}".encode()).hexdigest()[:32]
    if scheduled_at:
        when = _parse(scheduled_at)
        if when is None or when.tzinfo is None:
            raise ValueError("scheduled_at must be ISO-8601 with timezone")
        scheduled_at = _iso(when)
    approved = cleared(render)
    status = "WAITING_APPROVAL" if not approved else ("SCHEDULED" if scheduled_at else "QUEUED")
    opts = dict(options or {})
    meta = render.get("result") or {}
    opts.setdefault("duration_s", meta.get("duration"))
    jid = db.new_id()
    with db.connect() as conn:
        existing = conn.execute("SELECT id FROM publish_job WHERE idempotency_key=?", (key,)).fetchone()
        if existing:
            return get_publish_job(existing["id"])
        conn.execute(
            "INSERT INTO publish_job(id,render_job_id,account_id,platform,rendition,idempotency_key,status,"
            "scheduled_at,title,caption,tags,privacy,options,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (jid, render_job_id, account_id, plat, rendition, key, status, scheduled_at, title, caption,
             db.dumps(tags or []), privacy, db.dumps(opts), db.now(), db.now()))
        db.audit(actor, "publish.create", "publish_job", jid,
                 {"platform": plat, "status": status, "scheduled_at": scheduled_at}, conn)
    return get_publish_job(jid)


def get_publish_job(jid: str) -> dict | None:
    with db.connect() as conn:
        return db.row(conn.execute("SELECT * FROM publish_job WHERE id=?", (jid,)).fetchone(), PUB_JSON)


def list_publish_jobs(status: str | None = None, since: str | None = None, until: str | None = None) -> list[dict]:
    q, args = "SELECT * FROM publish_job WHERE 1=1", []
    if status:
        q += " AND status=?"
        args.append(status)
    if since:
        q += " AND COALESCE(scheduled_at, created_at) >= ?"
        args.append(since)
    if until:
        q += " AND COALESCE(scheduled_at, created_at) < ?"
        args.append(until)
    with db.connect() as conn:
        return [db.row(r, PUB_JSON) for r in conn.execute(q + " ORDER BY COALESCE(scheduled_at, created_at)", args)]


def _update(jid: str, actor: str = "worker", action: str | None = None, detail: dict | None = None, **fields) -> None:
    fields["updated_at"] = db.now()
    sets = ",".join(f"{k}=?" for k in fields)
    with db.connect() as conn:
        conn.execute(f"UPDATE publish_job SET {sets} WHERE id=?", (*fields.values(), jid))
        if action:
            db.audit(actor, action, "publish_job", jid, detail or {}, conn)


def cancel(jid: str, actor: str = "user") -> bool:
    j = get_publish_job(jid)
    if not j or j["status"] in ("PUBLISHED", "UPLOADING", "PROCESSING"):
        return False
    _update(jid, actor, "publish.cancel", status="CANCELLED")
    return True


def retry_now(jid: str, actor: str = "user") -> dict:
    j = get_publish_job(jid)
    if not j or j["status"] not in ("FAILED", "MANUAL_REQUIRED", "SCHEDULED"):
        raise ValueError("only failed/manual/scheduled jobs can be retried")
    render = jobs.get_job(j["render_job_id"])
    if not cleared(render):
        raise ValueError("render not approved")
    _update(jid, actor, "publish.retry", status="QUEUED", next_attempt_at=None, error=None, attempts=0,
            scheduled_at=None if j["status"] == "SCHEDULED" else j["scheduled_at"])
    return get_publish_job(jid)


def mark_published(jid: str, permalink: str, external_id: str | None, actor: str) -> dict:
    """Manual override: a human posted it (e.g. via OA Manager)."""
    _update(jid, actor, "publish.manual_mark", {"permalink": permalink}, status="PUBLISHED", permalink=permalink,
            external_id=external_id or "manual", error=None)
    return get_publish_job(jid)


def build_manual_package(job: dict, reason: str) -> dict:
    render = jobs.get_job(job["render_job_id"])
    files = jobs.result_files(render)
    out = config.STUDIO_DIR / "manual_packages" / job["id"]
    out.mkdir(parents=True, exist_ok=True)
    src = files.get(job["rendition"])
    pkg = {"reason": reason, "platform": job["platform"], "dir": str(out)}
    if src:
        shutil.copy2(src, out / f"video_{job['platform']}.mp4")
        pkg["video"] = str(out / f"video_{job['platform']}.mp4")
    if files.get("thumbnail"):
        shutil.copy2(files["thumbnail"], out / "thumbnail.jpg")
        pkg["thumbnail"] = str(out / "thumbnail.jpg")
    if files.get("srt"):
        shutil.copy2(files["srt"], out / "subtitles.srt")
    text = f"{job.get('title') or ''}\n\n{job.get('caption') or ''}\n\n" + " ".join(f"#{t.lstrip('#')}" for t in job.get("tags") or [])
    (out / "caption.txt").write_text(text.strip() + "\n", encoding="utf-8")
    pkg["caption"] = str(out / "caption.txt")
    return pkg


# ---------------------------------------------------------------- worker
def backoff_seconds(attempt: int, base: float = 30.0, cap: float = 3600.0) -> float:
    return min(cap, base * (2 ** max(0, attempt - 1))) * random.uniform(0.8, 1.2)


def _due(now: datetime) -> list[dict]:
    n = _iso(now)
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM publish_job WHERE ((status='QUEUED') OR (status='SCHEDULED' AND scheduled_at<=?) "
            "OR (status='PROCESSING')) AND (next_attempt_at IS NULL OR next_attempt_at<=?) "
            "ORDER BY COALESCE(scheduled_at, created_at)", (n, n)).fetchall()
    return [db.row(r, PUB_JSON) for r in rows]


def _last_publish(platform: str) -> datetime | None:
    with db.connect() as conn:
        r = conn.execute("SELECT MAX(updated_at) m FROM publish_job WHERE platform=? AND status IN "
                         "('PUBLISHED','UPLOADING')", (platform,)).fetchone()
    return _parse(r["m"]) if r and r["m"] else None


def process_job(job: dict) -> dict:
    jid, plat = job["id"], job["platform"]
    render = jobs.get_job(job["render_job_id"])
    if not cleared(render):
        _update(jid, status="WAITING_APPROVAL")
        return get_publish_job(jid)
    acc = get_account(job["account_id"])
    if not acc or acc["status"] != "ACTIVE":
        _update(jid, action="publish.fail", status="FAILED", error=f"account {acc and acc['status']}")
        return get_publish_job(jid)
    files = jobs.result_files(render)
    pub = publisher(plat)
    attempts = int(job["attempts"]) + (0 if job["status"] == "PROCESSING" else 1)
    ctx = PublishContext(
        job_id=jid, video=files.get(job["rendition"], Path("/nonexistent")), thumbnail=files.get("thumbnail"),
        title=job["title"] or "", caption=job["caption"] or "", tags=job["tags"] or [], privacy=job["privacy"],
        options=job["options"] or {}, credentials=_credentials(acc["id"]), account=acc, state=job["state"] or {},
        save_state=lambda s: _update(jid, state=db.dumps(s)),
        save_credentials=lambda c: _save_credentials(acc["id"], c))
    try:
        if job["status"] == "PROCESSING":
            out = pub.check_status(ctx)
            if out is None:
                _update(jid, next_attempt_at=_iso(datetime.now(timezone.utc) + timedelta(seconds=30)))
                return get_publish_job(jid)
        else:
            _update(jid, action="publish.start", status="UPLOADING", attempts=attempts)
            out = pub.publish(ctx)
        if out.status == "PROCESSING":
            _update(jid, status="PROCESSING", external_id=out.external_id,
                    next_attempt_at=_iso(datetime.now(timezone.utc) + timedelta(seconds=20)))
        else:
            _update(jid, action="publish.done", detail={"external_id": out.external_id, "permalink": out.permalink},
                    status="PUBLISHED", external_id=out.external_id, permalink=out.permalink, error=None,
                    next_attempt_at=None)
            notify("published", {"id": jid, "platform": plat, "permalink": out.permalink})
    except RetryableError as e:
        if attempts >= config.PUBLISH_MAX_ATTEMPTS:
            _update(jid, action="publish.fail", detail={"error": str(e)}, status="FAILED", error=str(e)[:1000])
            notify("publish_failed", {"id": jid, "platform": plat, "error": str(e)[:300]})
        else:
            wait = e.retry_after or backoff_seconds(attempts)
            resume = "PROCESSING" if job["status"] == "PROCESSING" else ("QUEUED" if not job["scheduled_at"] else "SCHEDULED")
            _update(jid, action="publish.retry_scheduled", detail={"error": str(e)[:300], "wait_s": round(wait)},
                    status=resume, error=str(e)[:1000],
                    next_attempt_at=_iso(datetime.now(timezone.utc) + timedelta(seconds=wait)))
    except AuthError as e:
        with db.connect() as conn:
            conn.execute("UPDATE social_account SET status='NEEDS_REAUTH', updated_at=? WHERE id=?", (db.now(), acc["id"]))
        _update(jid, action="publish.fail", detail={"error": "auth"}, status="FAILED", error=f"re-auth needed: {e}"[:1000])
        notify("account_reauth", {"account_id": acc["id"], "platform": plat})
    except ManualRequired as e:
        pkg = build_manual_package(job, str(e))
        _update(jid, action="publish.manual_required", detail={"reason": str(e)[:300]}, status="MANUAL_REQUIRED",
                error=str(e)[:1000], manual_package=db.dumps(pkg))
        notify("publish_manual", {"id": jid, "platform": plat, "reason": str(e)[:300]})
    except (PermanentError, OSError, KeyError, ValueError) as e:
        _update(jid, action="publish.fail", detail={"error": str(e)[:300]}, status="FAILED", error=str(e)[:1000])
        notify("publish_failed", {"id": jid, "platform": plat, "error": str(e)[:300]})
    except httpx.HTTPError as e:
        wait = backoff_seconds(attempts)
        _update(jid, status="QUEUED" if attempts < config.PUBLISH_MAX_ATTEMPTS else "FAILED", error=str(e)[:1000],
                next_attempt_at=_iso(datetime.now(timezone.utc) + timedelta(seconds=wait)))
    return get_publish_job(jid)


def tick(now: datetime | None = None, respect_interval: bool = True) -> list[dict]:
    """One scheduler pass: at most one job per platform (per-platform queue)."""
    now = now or datetime.now(timezone.utc)
    done, busy = [], set()
    for job in _due(now):
        plat = job["platform"]
        if plat in busy:
            continue
        if respect_interval and job["status"] != "PROCESSING":
            last = _last_publish(plat)
            if last and (now - last).total_seconds() < MIN_INTERVAL_S[plat]:
                continue
        busy.add(plat)
        done.append(process_job(job))
    return done


class PublishWorker:
    def __init__(self, poll_s: float = config.PUBLISH_POLL_S):
        self.poll_s = poll_s
        self._stop = threading.Event()
        self._t: threading.Thread | None = None

    def _loop(self) -> None:
        with db.connect() as conn:  # crash recovery: an UPLOADING job resumes from its checkpoints
            conn.execute("UPDATE publish_job SET status='QUEUED' WHERE status='UPLOADING'")
        while not self._stop.is_set():
            try:
                tick()
            except Exception:  # noqa: BLE001 - keep the scheduler alive
                logger.exception("publish tick failed")
            self._stop.wait(self.poll_s)

    def start(self) -> None:
        self._t = threading.Thread(target=self._loop, name="studio-publish", daemon=True)
        self._t.start()

    def stop(self) -> None:
        self._stop.set()
        if self._t:
            self._t.join(5)


def calendar(month: str) -> dict:
    """Publishing calendar for 'YYYY-MM' grouped by day."""
    start = datetime.fromisoformat(month + "-01").replace(tzinfo=timezone.utc)
    end = (start + timedelta(days=32)).replace(day=1)
    out: dict[str, list] = {}
    for j in list_publish_jobs(since=_iso(start), until=_iso(end)):
        day = (j["scheduled_at"] or j["created_at"])[:10]
        out.setdefault(day, []).append({k: j[k] for k in ("id", "platform", "status", "title", "scheduled_at",
                                                         "permalink", "render_job_id")})
    return out


def export_state() -> str:
    return json.dumps({"accounts": list_accounts(), "jobs": list_publish_jobs()}, ensure_ascii=False, default=str)
