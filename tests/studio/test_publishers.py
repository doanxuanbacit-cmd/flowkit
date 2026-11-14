"""Connector tests against mocked platform APIs (httpx.MockTransport).

These verify the request sequence, resumability and error mapping. They do
not prove the live APIs accept our app — that needs sandbox/test accounts.
"""
import json
from pathlib import Path

import httpx
import pytest

from studio.publish.base import AuthError, ManualRequired, PermanentError, PublishContext, RetryableError
from studio.publish.facebook import FacebookReelsPublisher
from studio.publish.tiktok import TikTokPublisher, plan_chunks
from studio.publish.youtube import YouTubeShortsPublisher, seconds_to_quota_reset
from studio.publish.zalo import ZaloOAPublisher


@pytest.fixture
def video(tmp_path):
    p = tmp_path / "v.mp4"
    p.write_bytes(b"\x00" * 3000)
    return p


def ctx(video, creds, **kw):
    saved = {}
    c = PublishContext(job_id="j1", video=video, thumbnail=None, title="Tiêu đề", caption="Mô tả #AI", tags=["ai"],
                       privacy=kw.pop("privacy", "public"), options=kw.pop("options", {}), credentials=creds,
                       account=kw.pop("account", {}), save_state=lambda s: saved.update(state=dict(s)),
                       save_credentials=lambda cr: saved.update(creds=cr))
    c.saved = saved
    return c


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------- Facebook
def test_facebook_reels_flow(video):
    calls = []

    def h(req: httpx.Request):
        calls.append((req.method, req.url.host, req.url.path))
        if req.url.host == "rupload.facebook.com":
            assert req.headers["authorization"] == "OAuth PAGE_TOKEN"
            assert req.headers["file_size"] == "3000" and req.headers["offset"] == "0"
            return httpx.Response(200, json={"success": True})
        if req.url.path.endswith("/video_reels"):
            body = dict(httpx.QueryParams(req.content.decode()))
            if body["upload_phase"] == "start":
                return httpx.Response(200, json={"video_id": "V1", "upload_url": "https://rupload.facebook.com/video-upload/v26.0/V1"})
            assert body["video_state"] == "PUBLISHED" and body["description"] == "Mô tả #AI"
            return httpx.Response(200, json={"success": True})
        if req.url.path.endswith("/V1"):
            return httpx.Response(200, json={"permalink_url": "/reel/V1", "status": {
                "video_status": "ready", "publishing_phase": {"status": "complete"}}})
        return httpx.Response(404)

    c = ctx(video, {"page_id": "P1", "page_access_token": "PAGE_TOKEN"})
    out = FacebookReelsPublisher(client(h)).publish(c)
    assert out.external_id == "V1" and out.permalink == "https://www.facebook.com/reel/V1"
    assert [x[2].split("/")[-1] for x in calls] == ["video_reels", "V1", "video_reels", "V1"]


def test_facebook_error_mapping(video):
    def h(req):
        return httpx.Response(400, json={"error": {"code": 190, "message": "Error validating access token"}})
    with pytest.raises(AuthError):
        FacebookReelsPublisher(client(h)).publish(ctx(video, {"page_id": "P", "page_access_token": "T"}))

    def h2(req):
        return httpx.Response(400, json={"error": {"code": 4, "message": "Application request limit reached"}})
    with pytest.raises(RetryableError):
        FacebookReelsPublisher(client(h2)).publish(ctx(video, {"page_id": "P", "page_access_token": "T"}))


def test_facebook_processing_error_is_permanent(video):
    c = ctx(video, {"page_id": "P", "page_access_token": "T"})
    c.state = {"video_id": "V9", "uploaded": True, "finished": True}

    def h(req):
        return httpx.Response(200, json={"status": {"video_status": "error", "processing_phase": {
            "status": "error", "errors": [{"message": "Resolution too low"}]}}})
    with pytest.raises(PermanentError, match="Resolution too low"):
        FacebookReelsPublisher(client(h)).publish(c)


# ---------------------------------------------------------------- TikTok
def test_tiktok_chunk_plan():
    assert plan_chunks(30_000_000) == (30_000_000, 1)
    cs, n = plan_chunks(150 * 1024 * 1024, 10 * 1024 * 1024)
    assert cs == 10 * 1024 * 1024 and n == 15


def test_tiktok_direct_post_flow(video):
    seen = {}

    def h(req):
        p = req.url.path
        if p.endswith("creator_info/query/"):
            return httpx.Response(200, json={"data": {"privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"],
                                                      "max_video_post_duration_sec": 600, "creator_username": "xb"},
                                             "error": {"code": "ok"}})
        if p.endswith("video/init/"):
            body = json.loads(req.content)
            seen["init"] = body
            return httpx.Response(200, json={"data": {"publish_id": "PUB1", "upload_url": "https://up.tiktok/x"},
                                             "error": {"code": "ok"}})
        if req.url.host == "up.tiktok":
            seen["range"] = req.headers["content-range"]
            return httpx.Response(201)
        if p.endswith("status/fetch/"):
            return httpx.Response(200, json={"data": {"status": "PUBLISH_COMPLETE",
                                                      "publicaly_available_post_id": [7300000000001]},
                                             "error": {"code": "ok"}})
        return httpx.Response(404)

    c = ctx(video, {"access_token": "act.x"}, account={"capabilities": {"direct_post": True, "creator_username": "xb"}},
            options={"duration_s": 30})
    out = TikTokPublisher(client(h)).publish(c)
    assert seen["init"]["source_info"] == {"source": "FILE_UPLOAD", "video_size": 3000, "chunk_size": 3000,
                                           "total_chunk_count": 1}
    assert seen["init"]["post_info"]["privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert seen["range"] == "bytes 0-2999/3000"
    assert out.permalink == "https://www.tiktok.com/@xb/video/7300000000001"


def test_tiktok_privacy_not_allowed_goes_manual(video):
    def h(req):
        return httpx.Response(200, json={"data": {"privacy_level_options": ["SELF_ONLY"]}, "error": {"code": "ok"}})
    with pytest.raises(ManualRequired):
        TikTokPublisher(client(h)).publish(ctx(video, {"access_token": "a"}))


def test_tiktok_unaudited_and_auth_errors(video):
    def h(req):
        return httpx.Response(401, json={"error": {"code": "access_token_invalid", "message": "bad"}})
    with pytest.raises(AuthError):
        TikTokPublisher(client(h)).publish(ctx(video, {"access_token": "a"}))


def test_tiktok_inbox_mode_requires_manual_finish(video):
    def h(req):
        p = req.url.path
        if p.endswith("creator_info/query/"):
            return httpx.Response(200, json={"data": {"privacy_level_options": ["PUBLIC_TO_EVERYONE"]}, "error": {"code": "ok"}})
        if p.endswith("inbox/video/init/"):
            return httpx.Response(200, json={"data": {"publish_id": "P", "upload_url": "https://up.tiktok/x"}, "error": {"code": "ok"}})
        if req.url.host == "up.tiktok":
            return httpx.Response(201)
        return httpx.Response(200, json={"data": {"status": "SEND_TO_USER_INBOX"}, "error": {"code": "ok"}})
    with pytest.raises(ManualRequired, match="inbox"):
        TikTokPublisher(client(h)).publish(ctx(video, {"access_token": "a"}, account={"capabilities": {"direct_post": False}}))


# ---------------------------------------------------------------- YouTube
def test_youtube_resumable_upload_with_308(video):
    state = {"puts": 0}

    def h(req):
        if req.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "ya29.new", "expires_in": 3600})
        if req.method == "POST" and req.url.path == "/upload/youtube/v3/videos":
            body = json.loads(req.content)
            assert body["snippet"]["title"].endswith("#Shorts")
            assert body["status"]["privacyStatus"] == "private" and body["status"]["publishAt"]
            return httpx.Response(200, headers={"location": "https://www.googleapis.com/upload/session/1"})
        if req.method == "PUT":
            state["puts"] += 1
            assert req.headers["authorization"] == "Bearer ya29.new"
            if state["puts"] == 1:
                return httpx.Response(308, headers={"range": "bytes=0-1499"})
            assert req.headers["content-range"] == "bytes 1500-2999/3000"
            return httpx.Response(200, json={"id": "YT123"})
        return httpx.Response(404)

    import studio.publish.youtube as yt
    old = yt.CHUNK
    yt.CHUNK = 10_000
    try:
        c = ctx(video, {"refresh_token": "r", "client_id": "c", "client_secret": "s"}, privacy="public",
                options={"publish_at": "2026-12-01T12:00:00Z"})
        out = YouTubeShortsPublisher(client(h)).publish(c)
    finally:
        yt.CHUNK = old
    assert out.external_id == "YT123" and out.permalink.endswith("/shorts/YT123")
    assert c.saved["creds"]["access_token"] == "ya29.new"


def test_youtube_quota_is_retryable_until_reset(video):
    def h(req):
        if req.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(403, json={"error": {"errors": [{"reason": "quotaExceeded"}]}})
    with pytest.raises(RetryableError) as e:
        YouTubeShortsPublisher(client(h)).publish(ctx(video, {"refresh_token": "r", "client_id": "c", "client_secret": "s"}))
    assert 0 < e.value.retry_after <= 25 * 3600
    assert 0 < seconds_to_quota_reset() <= 25 * 3600


def test_youtube_refresh_revoked_is_auth_error(video):
    def h(req):
        return httpx.Response(400, json={"error": "invalid_grant"})
    with pytest.raises(AuthError):
        YouTubeShortsPublisher(client(h)).publish(ctx(video, {"refresh_token": "r", "client_id": "c", "client_secret": "s"}))


# ---------------------------------------------------------------- Zalo
def test_zalo_video_article_flow(video):
    seq = []

    def h(req):
        seq.append(req.url.path)
        assert req.headers["access_token"] == "OA_TOKEN"
        if req.url.path.endswith("preparevideo"):
            return httpx.Response(200, json={"error": 0, "data": {"token": "VT"}})
        if req.url.path.endswith("upload_video/verify"):
            return httpx.Response(200, json={"error": 0, "data": {"status": 1, "video_id": "ZV1"}})
        if req.url.path.endswith("article/create"):
            body = json.loads(req.content)
            assert body["type"] == "video" and body["video_id"] == "ZV1"
            return httpx.Response(200, json={"error": 0, "data": {"token": "AT"}})
        if req.url.path.endswith("article/verify"):
            return httpx.Response(200, json={"error": 0, "data": {"id": "ART9"}})
        return httpx.Response(404)

    out = ZaloOAPublisher(client(h)).publish(ctx(video, {"access_token": "OA_TOKEN"}))
    assert out.external_id == "ART9"
    assert seq[0].endswith("preparevideo") and seq[-1].endswith("article/verify")


def test_zalo_processing_retries_then_permission_manual(video):
    def h(req):
        if req.url.path.endswith("preparevideo"):
            return httpx.Response(200, json={"error": 0, "data": {"token": "VT"}})
        return httpx.Response(200, json={"error": 0, "data": {"status": 3, "convert_percent": 40}})
    c = ctx(video, {"access_token": "OA_TOKEN"})
    with pytest.raises(RetryableError):
        ZaloOAPublisher(client(h)).publish(c)
    assert c.state["video_token"] == "VT"  # resumes without re-uploading

    with pytest.raises(ManualRequired):
        ZaloOAPublisher(client(h)).publish(ctx(video, {"access_token": "t"},
                                               account={"capabilities": {"article_api": False}}))


def test_zalo_size_limit_manual(tmp_path):
    big = tmp_path / "big.mp4"
    with open(big, "wb") as f:
        f.truncate(51 * 1024 * 1024)
    with pytest.raises(ManualRequired):
        ZaloOAPublisher(client(lambda r: httpx.Response(500))).publish(ctx(big, {"access_token": "t"}))
