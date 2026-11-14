"""Studio database: SQLite with numbered migrations (PostgreSQL-ready schema).

Kept separate from FlowKit's flow_agent.db so Studio can be upgraded, backed up
or moved to Postgres without touching the generation pipeline. Plain sqlite3 in
WAL mode; one connection per call is cheap and thread-safe for the workers.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from studio import config

MIGRATIONS: list[tuple[int, str, str]] = [
    (1, "core", """
CREATE TABLE asset (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('video','audio','image','subtitle','lut','other')),
    path TEXT NOT NULL,
    original_name TEXT,
    checksum TEXT NOT NULL UNIQUE,
    duration REAL, width INTEGER, height INTEGER, fps REAL, has_audio INTEGER DEFAULT 0,
    size_bytes INTEGER,
    source TEXT NOT NULL DEFAULT 'upload' CHECK(source IN ('upload','flowkit','render','import')),
    flowkit_project_id TEXT, flowkit_video_id TEXT, flowkit_scene_id TEXT,
    meta TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX idx_asset_scene ON asset(flowkit_scene_id);
CREATE TABLE timeline (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    flowkit_video_id TEXT,
    data TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE render_job (
    id TEXT PRIMARY KEY,
    timeline_id TEXT NOT NULL REFERENCES timeline(id) ON DELETE CASCADE,
    timeline_version INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'QUEUED'
        CHECK(status IN ('QUEUED','RUNNING','COMPLETED','FAILED','CANCELLED')),
    stage TEXT, progress REAL NOT NULL DEFAULT 0,
    options TEXT NOT NULL DEFAULT '{}',
    result TEXT, error TEXT,
    qa_passed INTEGER,
    approval TEXT NOT NULL DEFAULT 'PENDING' CHECK(approval IN ('PENDING','APPROVED','REJECTED')),
    approved_by TEXT, approval_note TEXT, approved_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT
);
CREATE INDEX idx_render_status ON render_job(status);
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
    entity TEXT NOT NULL, entity_id TEXT, detail TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_audit_entity ON audit_log(entity, entity_id);
"""),
    (2, "publishing", """
CREATE TABLE social_account (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL CHECK(platform IN ('facebook','youtube','tiktok','zalo')),
    name TEXT NOT NULL,
    external_id TEXT,
    credentials_enc TEXT,
    scopes TEXT NOT NULL DEFAULT '[]',
    capabilities TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','NEEDS_REAUTH','DISABLED')),
    token_expires_at TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE publish_job (
    id TEXT PRIMARY KEY,
    render_job_id TEXT NOT NULL REFERENCES render_job(id),
    account_id TEXT NOT NULL REFERENCES social_account(id),
    platform TEXT NOT NULL,
    rendition TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'WAITING_APPROVAL' CHECK(status IN (
        'WAITING_APPROVAL','SCHEDULED','QUEUED','UPLOADING','PROCESSING','PUBLISHED',
        'FAILED','CANCELLED','MANUAL_REQUIRED')),
    scheduled_at TEXT,
    title TEXT, caption TEXT, tags TEXT NOT NULL DEFAULT '[]',
    privacy TEXT NOT NULL DEFAULT 'private',
    options TEXT NOT NULL DEFAULT '{}',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    external_id TEXT, permalink TEXT,
    state TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    manual_package TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX idx_publish_status ON publish_job(status, next_attempt_at);
CREATE TABLE metric_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    publish_job_id TEXT NOT NULL REFERENCES publish_job(id) ON DELETE CASCADE,
    fetched_at TEXT NOT NULL,
    views INTEGER, likes INTEGER, comments INTEGER, shares INTEGER,
    raw TEXT NOT NULL DEFAULT '{}'
);
"""),
]

_lock = threading.Lock()
_ready: set[str] = set()


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def new_id() -> str:
    return str(uuid.uuid4())


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def migrate(path: Path | None = None) -> int:
    path = Path(path or config.STUDIO_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT)")
        done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        for ver, name, sql in MIGRATIONS:
            if ver in done:
                continue
            conn.execute("BEGIN")
            try:
                for stmt in [s for s in sql.split(";") if s.strip()]:
                    conn.execute(stmt)
                conn.execute("INSERT INTO schema_migrations VALUES (?,?,?)", (ver, name, now()))
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return max([v for v, _, _ in MIGRATIONS])
    finally:
        conn.close()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    key = str(config.STUDIO_DB)
    if key not in _ready:
        with _lock:
            if key not in _ready:
                migrate(config.STUDIO_DB)
                _ready.add(key)
    conn = _connect(config.STUDIO_DB)
    try:
        yield conn
    finally:
        conn.close()


def row(r: sqlite3.Row | None, json_fields: tuple[str, ...] = ()) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for f in json_fields:
        if d.get(f) is not None:
            try:
                d[f] = json.loads(d[f])
            except (TypeError, ValueError):
                pass
    return d


def audit(actor: str, action: str, entity: str, entity_id: str | None, detail: dict | None = None,
          conn: sqlite3.Connection | None = None) -> None:
    """Append-only audit trail. Callers must never put secrets in `detail`."""
    sql = "INSERT INTO audit_log(ts, actor, action, entity, entity_id, detail) VALUES (?,?,?,?,?,?)"
    args = (now(), actor, action, entity, entity_id, json.dumps(detail or {}, ensure_ascii=False))
    if conn is not None:
        conn.execute(sql, args)
        return
    with connect() as c:
        c.execute(sql, args)


def dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)
