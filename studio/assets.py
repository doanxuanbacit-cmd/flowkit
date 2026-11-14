"""Media Asset Manager — content-addressed storage with probe metadata.

Files are copied to ASSETS_DIR/<sha[:2]>/<sha><ext>; importing the same bytes
twice returns the existing asset (no duplicate storage, no re-probe).
"""
from __future__ import annotations

import shutil
from pathlib import Path

from studio import config, db
from studio.editing import ffmpeg

KIND_BY_EXT = {
    ".mp4": "video", ".mov": "video", ".mkv": "video", ".webm": "video", ".m4v": "video",
    ".wav": "audio", ".mp3": "audio", ".m4a": "audio", ".aac": "audio", ".flac": "audio", ".ogg": "audio",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image",
    ".srt": "subtitle", ".ass": "subtitle", ".cube": "lut",
}
JSON_FIELDS = ("meta",)


def kind_for(path: str | Path) -> str:
    return KIND_BY_EXT.get(Path(path).suffix.lower(), "other")


def import_file(path: str | Path, *, source: str = "upload", original_name: str | None = None,
                move: bool = False, flowkit: dict | None = None, meta: dict | None = None) -> dict:
    src = Path(path)
    if not src.is_file():
        raise FileNotFoundError(str(src))
    checksum = ffmpeg.sha256_file(src)
    with db.connect() as conn:
        existing = conn.execute("SELECT * FROM asset WHERE checksum=?", (checksum,)).fetchone()
        if existing:
            if move:
                src.unlink(missing_ok=True)
            return db.row(existing, JSON_FIELDS)
    kind = kind_for(original_name or src.name)
    config.ensure_dirs()
    dest = config.ASSETS_DIR / checksum[:2] / f"{checksum}{Path(original_name or src.name).suffix.lower()}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        (shutil.move if move else shutil.copy2)(str(src), str(dest))
    elif move:
        src.unlink(missing_ok=True)
    info = None
    if kind in ("video", "audio", "image"):
        try:
            info = ffmpeg.probe(dest)
        except ffmpeg.FFmpegError as e:
            dest.unlink(missing_ok=True)
            raise ValueError(f"unreadable media: {e}") from e
    fk = flowkit or {}
    rec = dict(id=db.new_id(), kind=kind, path=str(dest), original_name=original_name or src.name,
               checksum=checksum, duration=info.duration if info else None, width=info.width if info else None,
               height=info.height if info else None, fps=info.fps if info else None,
               has_audio=int(info.has_audio) if info else 0, size_bytes=dest.stat().st_size, source=source,
               flowkit_project_id=fk.get("project_id"), flowkit_video_id=fk.get("video_id"),
               flowkit_scene_id=fk.get("scene_id"), meta=db.dumps(meta or {}), created_at=db.now())
    with db.connect() as conn:
        conn.execute(f"INSERT INTO asset({','.join(rec)}) VALUES ({','.join('?' * len(rec))})", tuple(rec.values()))
        db.audit("system", "asset.import", "asset", rec["id"], {"source": source, "kind": kind}, conn)
        return db.row(conn.execute("SELECT * FROM asset WHERE id=?", (rec["id"],)).fetchone(), JSON_FIELDS)


def get(asset_id: str) -> dict | None:
    with db.connect() as conn:
        return db.row(conn.execute("SELECT * FROM asset WHERE id=?", (asset_id,)).fetchone(), JSON_FIELDS)


def list_assets(kind: str | None = None, limit: int = 200, offset: int = 0) -> list[dict]:
    with db.connect() as conn:
        if kind:
            rows = conn.execute("SELECT * FROM asset WHERE kind=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                                (kind, limit, offset)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM asset ORDER BY created_at DESC LIMIT ? OFFSET ?",
                                (limit, offset)).fetchall()
        return [db.row(r, JSON_FIELDS) for r in rows]


def delete(asset_id: str) -> bool:
    a = get(asset_id)
    if not a:
        return False
    with db.connect() as conn:
        conn.execute("DELETE FROM asset WHERE id=?", (asset_id,))
        db.audit("user", "asset.delete", "asset", asset_id, {}, conn)
    Path(a["path"]).unlink(missing_ok=True)
    return True
