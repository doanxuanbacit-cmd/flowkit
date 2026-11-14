"""DB migrations, render jobs, approval gate, publishing queue and HTTP API."""
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from studio import config, db, jobs
from studio.editing.engine import RenderOptions
from studio.editing.schemas import Platform, Timeline, TimelineClip
from studio.publish import service as pub
from studio.publish.base import AuthError, ManualRequired, PublishOutcome, Publisher, RetryableError


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STUDIO_DIR", tmp_path / "studio")
    monkeypatch.setattr(config, "STUDIO_DB", tmp_path / "studio" / "studio.db")
    monkeypatch.setattr(config, "ASSETS_DIR", tmp_path / "studio" / "assets")
    monkeypatch.setattr(config, "RENDERS_DIR", tmp_path / "studio" / "renders")
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "studio" / "cache")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "flowkit_output")
    monkeypatch.setattr(config, "SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(config, "REQUIRE_APPROVAL", True)
    config.ensure_dirs()
    pub._factory.clear()
    yield tmp_path
    pub._factory.clear()


class FakePublisher(Publisher):
    platform = "tiktok"

    def __init__(self, behaviour):
        self.behaviour = list(behaviour)
        self.calls = 0

    def publish(self, ctx):
        self.calls += 1
        b = self.behaviour.pop(0) if self.behaviour else "ok"
        if isinstance(b, Exception):
            raise b
        ctx.checkpoint(step="done")
        return PublishOutcome(external_id="EXT1", permalink="https://x/EXT1")

    def capabilities(self, creds, account):
        return {"direct_post": True}


def _render(media, env):
    from studio import assets
    a = assets.import_file(media.clip("s1", dur=1.5, size="270x480"))
    b = assets.import_file(media.clip("s2", dur=1.5, size="270x480", color="blue"))
    tl = Timeline(id="tl1", width=270, height=480, renditions=[Platform.tiktok],
                  clips=[TimelineClip(id="a", source=a["path"]), TimelineClip(id="b", source=b["path"])])
    jobs.save_timeline(tl)
    j = jobs.enqueue_render("tl1", RenderOptions(preset="ultrafast"))
    claimed = jobs._claim()
    assert claimed["id"] == j["id"] and claimed["status"] == "RUNNING"
    return jobs.run_job(claimed)


def test_migrations_idempotent(env):
    assert db.migrate() == 2 and db.migrate() == 2
    conn = sqlite3.connect(config.STUDIO_DB)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"asset", "timeline", "render_job", "social_account", "publish_job", "audit_log"} <= tables


def test_asset_dedupe(env, media):
    from studio import assets
    p = media.clip("d", dur=1)
    a1, a2 = assets.import_file(p), assets.import_file(p)
    assert a1["id"] == a2["id"] and a1["kind"] == "video" and a1["duration"] == pytest.approx(1, abs=0.1)


def test_full_publish_flow_with_gate_idempotency_and_retry(env, media):
    job = _render(media, env)
    assert job["status"] == "COMPLETED", job["error"]
    assert "tiktok" in jobs.result_files(job)

    fake = FakePublisher([RetryableError("429", retry_after=0.01), "ok"])
    pub.set_publisher("tiktok", fake)
    acc = pub.connect_account("tiktok", "Xuân Bắc AI", {"access_token": "act.secret", "scope": "video.publish"})
    assert "credentials_enc" not in acc and acc["capabilities"] == {"direct_post": True}
    raw = sqlite3.connect(config.STUDIO_DB).execute("SELECT credentials_enc FROM social_account").fetchone()[0]
    assert "act.secret" not in raw  # encrypted at rest

    pj = pub.create_publish_job(job["id"], acc["id"], caption="Phim mẫu #AI")
    assert pj["status"] == "WAITING_APPROVAL"
    assert pub.create_publish_job(job["id"], acc["id"])["id"] == pj["id"]  # idempotent
    assert pub.tick(respect_interval=False) == []  # gate holds

    if not job["qa_passed"]:
        with pytest.raises(ValueError):
            jobs.decide(job["id"], True, "Anh Bắc")
    jobs.decide(job["id"], True, "Anh Bắc", note="ok" if not job["qa_passed"] else "")
    assert pub.get_publish_job(pj["id"])["status"] == "QUEUED"

    r1 = pub.tick(respect_interval=False)[0]
    assert r1["status"] == "QUEUED" and r1["attempts"] == 1 and "429" in r1["error"]
    later = datetime.now(timezone.utc) + timedelta(seconds=5)
    r2 = pub.tick(now=later, respect_interval=False)[0]
    assert r2["status"] == "PUBLISHED" and r2["permalink"] == "https://x/EXT1" and r2["state"] == {"step": "done"}
    actions = [r[0] for r in sqlite3.connect(config.STUDIO_DB).execute(
        "SELECT action FROM audit_log WHERE entity_id=? ORDER BY id", (pj["id"],))]
    assert actions[0] == "publish.create" and "publish.retry_scheduled" in actions and actions[-1] == "publish.done"


def test_manual_auth_and_scheduling(env, media):
    job = _render(media, env)
    jobs.decide(job["id"], True, "Anh Bắc", note="override for test")
    pub.set_publisher("tiktok", FakePublisher([ManualRequired("privacy not allowed")]))
    acc = pub.connect_account("tiktok", "a", {"access_token": "t"}, verify=False)
    pj = pub.create_publish_job(job["id"], acc["id"], title="T", caption="C", tags=["ai"])
    out = pub.tick(respect_interval=False)[0]
    assert out["status"] == "MANUAL_REQUIRED"
    pkg = out["manual_package"]
    assert Path(pkg["video"]).exists() and "#ai" in Path(pkg["caption"]).read_text(encoding="utf-8")
    done = pub.mark_published(pj["id"], "https://tiktok.com/@x/video/1", None, "Anh Bắc")
    assert done["status"] == "PUBLISHED"

    # auth failure flags the account
    pub.set_publisher("tiktok", FakePublisher([AuthError("revoked")]))
    when = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    pj2 = pub.create_publish_job(job["id"], acc["id"], scheduled_at=when, idempotency_key="k2")
    assert pj2["status"] == "SCHEDULED"
    assert pub.tick(respect_interval=False) == []  # not due yet
    out2 = pub.tick(now=datetime.now(timezone.utc) + timedelta(hours=2), respect_interval=False)[0]
    assert out2["status"] == "FAILED" and pub.get_account(acc["id"])["status"] == "NEEDS_REAUTH"
    cal = pub.calendar(when[:7])
    assert any(j["id"] == pj2["id"] for d in cal.values() for j in d)


def test_rejected_render_cannot_publish(env, media):
    job = _render(media, env)
    jobs.decide(job["id"], False, "Anh Bắc", "hook yếu")
    acc = pub.connect_account("tiktok", "a", {"access_token": "t"}, verify=False)
    with pytest.raises(ValueError):
        pub.create_publish_job(job["id"], acc["id"])


def test_api_endpoints(env, media):
    from studio.api import router
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)
    h = c.get("/api/studio/health").json()
    assert h["ok"] and h["skills"] == 12 and h["db_version"] == 2
    assert len(c.get("/api/studio/skills").json()) == 12
    clip = media.clip("up", dur=1.2, size="270x480")
    with open(clip, "rb") as f:
        a = c.post("/api/studio/assets/upload", files={"file": ("cảnh 1.mp4", f, "video/mp4")}).json()
    assert a["kind"] == "video" and a["original_name"] == "cảnh 1.mp4"
    assert c.get(f"/api/studio/assets/{a['id']}/file").status_code == 200
    # analysis skill through the API, path allow-list enforced
    r = c.post("/api/studio/skills/edit-smart-cut/run", json={"path": a["path"]})
    assert r.status_code == 200 and "keep" in r.json()
    assert c.post("/api/studio/skills/edit-smart-cut/run", json={"path": "/etc/passwd"}).status_code == 403
    assert c.post("/api/studio/skills/edit-transition-pro/run", json={}).status_code == 400
    tl = {"id": "t9", "width": 270, "height": 480, "clips": [{"id": "x", "source": a["path"]}], "renditions": []}
    assert c.put("/api/studio/timelines/t9", json=tl).json()["version"] == 1
    assert c.put("/api/studio/timelines/t9", json=tl).json()["version"] == 2
    bad = dict(tl, clips=[{"id": "x", "source": "/etc/hosts"}])
    assert c.put("/api/studio/timelines/t9", json=bad).status_code == 403
    assert "001  AX" in c.get("/api/studio/timelines/t9/edl").text
    rj = c.post("/api/studio/timelines/t9/render", json={"preset": "ultrafast"}).json()
    assert rj["status"] == "QUEUED"
    jobs.run_job(jobs._claim())
    done = c.get(f"/api/studio/renders/{rj['id']}").json()
    assert done["status"] == "COMPLETED"
    assert c.get(f"/api/studio/renders/{rj['id']}/files/master").status_code == 200
    assert "master" in c.get(f"/api/studio/renders/{rj['id']}/qa").json()
    rv = c.post(f"/api/studio/renders/{rj['id']}/review", json={"approve": False, "reviewer": "Bắc", "note": "x"})
    assert rv.json()["approval"] == "REJECTED"
    ov = c.get("/api/studio/overview").json()
    assert ov["renders"]["COMPLETED"] == 1 and ov["timelines"] == 1
    assert c.get("/api/studio/analytics").json()["items"] == []
    assert any(x["action"] == "render.reject" for x in c.get("/api/studio/audit").json())
