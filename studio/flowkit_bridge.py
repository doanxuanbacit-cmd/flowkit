"""Bridge FlowKit Core → Studio: import a FlowKit video's scene clips as assets
and draft a Timeline from its scenes. No manual download/upload between Claude
and Google Flow: clips FlowKit already saved locally are picked up directly;
signed remote URLs are fetched once and content-addressed.

Read-only against FlowKit's database (opened with mode=ro).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

from agent.utils.paths import file_url_to_path, resolve_4k_file
from agent.utils.slugify import slugify
from studio import assets, config
from studio.editing.schemas import SubtitleCue, Timeline, TimelineClip

logger = logging.getLogger(__name__)
ALLOWED_REMOTE_HOSTS = (".googleusercontent.com", ".googleapis.com", ".google.com", ".ggpht.com")


class BridgeError(RuntimeError):
    pass


def _flow_db(path: Path | None = None) -> sqlite3.Connection:
    p = Path(path or config.FLOWKIT_DB_PATH)
    if not p.exists():
        raise BridgeError(f"FlowKit database not found at {p}")
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def list_videos(db_path: Path | None = None) -> list[dict]:
    with _flow_db(db_path) as conn:
        rows = conn.execute(
            "SELECT v.id, v.title, v.status, v.project_id, p.name AS project_name, "
            "(SELECT COUNT(*) FROM scene s WHERE s.video_id=v.id) AS scenes "
            "FROM video v JOIN project p ON p.id=v.project_id ORDER BY v.updated_at DESC").fetchall()
        return [dict(r) for r in rows]


def _download(url: str, tmp: Path) -> Path:
    host = urlparse(url).hostname or ""
    if not config.ALLOW_REMOTE_DOWNLOAD or not any(host.endswith(h) for h in ALLOWED_REMOTE_HOSTS):
        raise BridgeError(f"remote download not allowed for host {host!r}")
    dest = tmp / f"dl_{abs(hash(url)) % 10**10}.mp4"
    with httpx.stream("GET", url, timeout=120, follow_redirects=True) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    return dest


def resolve_scene_file(scene: dict, orientation: str, project_slug: str, tmp: Path) -> Path | None:
    o = orientation.lower()
    # Highest quality first: local upscale/4K, then the generated clip.
    for key in (f"{o}_upscale_url", f"{o}_video_url"):
        url = scene.get(key)
        if not url:
            continue
        local = file_url_to_path(url)
        if local is not None and local.is_file():
            return local
        if urlparse(url).scheme in ("http", "https"):
            try:
                return _download(url, tmp)
            except (BridgeError, httpx.HTTPError) as e:
                logger.warning("scene %s: %s", scene["id"], e)
        elif Path(url).is_file():
            return Path(url)
    k4 = resolve_4k_file(project_slug, scene.get("display_order") or 0, scene["id"])
    return k4


def import_video(video_id: str, orientation: str = "VERTICAL", db_path: Path | None = None) -> dict:
    """Import every scene clip of a FlowKit video. Returns assets + a draft timeline."""
    orientation = orientation.upper()
    with _flow_db(db_path) as conn:
        v = conn.execute("SELECT v.*, p.name AS project_name FROM video v JOIN project p ON p.id=v.project_id "
                         "WHERE v.id=?", (video_id,)).fetchone()
        if not v:
            raise BridgeError(f"FlowKit video {video_id} not found")
        scenes = [dict(r) for r in conn.execute(
            "SELECT * FROM scene WHERE video_id=? ORDER BY display_order", (video_id,)).fetchall()]
    v = dict(v)
    slug = slugify(v.get("project_name") or "project")
    imported, missing, clips, subs = [], [], [], []
    t = 0.0
    with tempfile.TemporaryDirectory(prefix="studio_fk_") as td:
        for sc in scenes:
            f = resolve_scene_file(sc, orientation, slug, Path(td))
            if not f:
                missing.append(sc["id"])
                continue
            a = assets.import_file(f, source="flowkit", original_name=f"scene_{sc.get('display_order') or 0:03d}.mp4",
                                   flowkit={"project_id": v["project_id"], "video_id": video_id, "scene_id": sc["id"]},
                                   meta={"orientation": orientation, "prompt": (sc.get("video_prompt") or "")[:500]})
            imported.append(a)
            try:
                chars = json.loads(sc.get("character_names") or "[]")
            except ValueError:
                chars = []
            clips.append(TimelineClip(id=f"sc_{sc['id'][:8]}", source=a["path"], label=f"Scene {sc.get('display_order')}",
                                      characters=[str(c) for c in chars if c]))
            dur = float(a.get("duration") or 0)
            if sc.get("narrator_text") and dur > 0.6:
                subs.append(SubtitleCue(start=round(t + 0.1, 3), end=round(t + dur - 0.1, 3), text=sc["narrator_text"]))
            t += dur
    if not clips:
        raise BridgeError(f"no usable {orientation.lower()} clips ({len(missing)} scenes missing video)")
    w, h = (1080, 1920) if orientation == "VERTICAL" else (1920, 1080)
    tl = Timeline(id=f"tl_{video_id[:8]}", title=v.get("title") or "", width=w, height=h, clips=clips,
                  subtitles=subs)
    return {"video": {"id": video_id, "title": v.get("title"), "project": v.get("project_name")},
            "assets": imported, "missing_scenes": missing, "timeline": tl.model_dump(mode="json")}
