"""TikTok publisher — Content Posting API (Direct Post, FILE_UPLOAD).

1. POST /v2/post/publish/creator_info/query/  (mandatory; gives privacy options + max duration)
2. POST /v2/post/publish/video/init/  source=FILE_UPLOAD  -> publish_id, upload_url
3. PUT chunks to upload_url with Content-Range
4. POST /v2/post/publish/status/fetch/  until PUBLISH_COMPLETE / FAILED

Needs scope `video.publish` on an audited app for public posts. Unaudited
clients may only post privately; if the token lacks video.publish, the job
falls back to the Upload (inbox draft) endpoint with `video.upload`, which
requires the creator to finish the post in the TikTok app → MANUAL_REQUIRED.
Credentials: {"access_token", "refresh_token", "open_id", "client_key", "client_secret"}.
"""
from __future__ import annotations

import math
import os
import time

from studio.publish.base import (
    AuthError, ManualRequired, PermanentError, PublishContext, PublishOutcome, Publisher, RetryableError,
)

API = os.environ.get("TIKTOK_API_BASE", "https://open.tiktokapis.com")
MIN_CHUNK = 5 * 1024 * 1024
MAX_CHUNK = 64 * 1024 * 1024
DEFAULT_CHUNK = 10 * 1024 * 1024
AUTH_CODES = {"access_token_invalid", "scope_not_authorized", "token_expired"}
RETRY_CODES = {"rate_limit_exceeded", "internal_error", "spam_risk_too_many_pending_share"}


def plan_chunks(size: int, chunk: int = DEFAULT_CHUNK) -> tuple[int, int]:
    """(chunk_size, total_chunk_count) per the media transfer rules: files under
    64 MB go in one chunk; otherwise 5–64 MB chunks and the final chunk absorbs
    the remainder (so it may be up to 2x chunk_size)."""
    if size <= MAX_CHUNK:
        return size, 1
    chunk = max(MIN_CHUNK, min(MAX_CHUNK, chunk))
    return chunk, max(1, size // chunk)


class TikTokPublisher(Publisher):
    platform = "tiktok"

    def _post(self, path: str, token: str, json: dict | None = None) -> dict:
        r = self.http.post(f"{API}{path}", headers={"Authorization": f"Bearer {token}",
                                                    "Content-Type": "application/json; charset=UTF-8"}, json=json or {})
        try:
            body = r.json()
        except ValueError:
            body = {}
        code = ((body.get("error") or {}).get("code")) if isinstance(body, dict) else None
        if code and code != "ok":
            msg = f"tiktok {code}: {(body.get('error') or {}).get('message', '')[:240]}"
            if code in AUTH_CODES:
                raise AuthError(msg)
            if code in RETRY_CODES:
                raise RetryableError(msg, 60)
            if code.startswith("unaudited_client") or code == "privacy_level_option_mismatch":
                raise ManualRequired(msg)
            raise PermanentError(msg)
        self.raise_for(r)
        return body.get("data") or {}

    def refresh(self, creds: dict) -> dict:
        r = self.http.post(f"{API}/v2/oauth/token/", data={
            "client_key": creds["client_key"], "client_secret": creds["client_secret"],
            "grant_type": "refresh_token", "refresh_token": creds["refresh_token"]},
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        body = self.raise_for(r)
        if "access_token" not in body:
            raise AuthError(f"refresh failed: {body.get('error_description') or body.get('error')}")
        return {**creds, "access_token": body["access_token"],
                "refresh_token": body.get("refresh_token", creds["refresh_token"]),
                "expires_at": time.time() + float(body.get("expires_in", 86400))}

    def _token(self, ctx: PublishContext) -> str:
        c = ctx.credentials
        if c.get("expires_at") and float(c["expires_at"]) - time.time() < 300 and c.get("refresh_token"):
            ctx.credentials = self.refresh(c)
            ctx.save_credentials(ctx.credentials)
        return ctx.credentials["access_token"]

    def capabilities(self, creds: dict, account: dict) -> dict:
        info = self._post("/v2/post/publish/creator_info/query/", creds["access_token"])
        scopes = set((creds.get("scope") or "").replace(" ", "").split(","))
        return {"direct_post": "video.publish" in scopes, "inbox_upload": "video.upload" in scopes,
                "privacy_level_options": info.get("privacy_level_options", []),
                "max_video_post_duration_sec": info.get("max_video_post_duration_sec"),
                "creator_username": info.get("creator_username")}

    def publish(self, ctx: PublishContext) -> PublishOutcome:
        self.preflight(ctx)
        token = self._token(ctx)
        st = ctx.state
        size = ctx.video.stat().st_size
        caps = ctx.account.get("capabilities") or {}
        direct = caps.get("direct_post", True)
        if not st.get("publish_id"):
            info = self._post("/v2/post/publish/creator_info/query/", token)
            options = info.get("privacy_level_options") or []
            want = ctx.options.get("privacy_level") or {"public": "PUBLIC_TO_EVERYONE", "private": "SELF_ONLY",
                                                         "friends": "MUTUAL_FOLLOW_FRIENDS"}.get(ctx.privacy, "SELF_ONLY")
            if options and want not in options:
                raise ManualRequired(f"privacy {want} not allowed for this creator (allowed: {options})")
            maxd = info.get("max_video_post_duration_sec")
            dur = ctx.options.get("duration_s")
            if maxd and dur and dur > maxd:
                raise PermanentError(f"video {dur:.0f}s exceeds creator max {maxd}s")
            chunk, count = plan_chunks(size, ctx.options.get("chunk_size", DEFAULT_CHUNK))
            source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}
            if direct:
                body = {"post_info": {"title": ctx.caption[:2200], "privacy_level": want,
                                      "disable_comment": bool(ctx.options.get("disable_comment", False)),
                                      "disable_duet": bool(ctx.options.get("disable_duet", False)),
                                      "disable_stitch": bool(ctx.options.get("disable_stitch", False)),
                                      "video_cover_timestamp_ms": int(ctx.options.get("cover_ms", 1000)),
                                      "brand_content_toggle": bool(ctx.options.get("brand_content", False)),
                                      "brand_organic_toggle": bool(ctx.options.get("brand_organic", False)),
                                      "is_aigc": bool(ctx.options.get("is_aigc", True))},
                        "source_info": source}
                data = self._post("/v2/post/publish/video/init/", token, body)
            else:
                data = self._post("/v2/post/publish/inbox/video/init/", token, {"source_info": source})
            ctx.checkpoint(publish_id=data["publish_id"], upload_url=data["upload_url"], chunk=chunk, count=count,
                           next_chunk=0, mode="direct" if direct else "inbox")
        if st.get("next_chunk", 0) < st["count"]:
            with open(ctx.video, "rb") as f:
                for i in range(st.get("next_chunk", 0), st["count"]):
                    first = i * st["chunk"]
                    last = size - 1 if i == st["count"] - 1 else first + st["chunk"] - 1
                    f.seek(first)
                    payload = f.read(last - first + 1)
                    r = self.http.put(st["upload_url"], content=payload, headers={
                        "Content-Type": "video/mp4", "Content-Length": str(len(payload)),
                        "Content-Range": f"bytes {first}-{last}/{size}"})
                    if r.status_code not in (200, 201, 206):
                        self.raise_for(r)
                        raise RetryableError(f"chunk {i} upload returned {r.status_code}")
                    ctx.checkpoint(next_chunk=i + 1)
        return self.check_status(ctx) or PublishOutcome(external_id=st["publish_id"], status="PROCESSING")

    def check_status(self, ctx: PublishContext) -> PublishOutcome | None:
        pid = ctx.state.get("publish_id")
        if not pid:
            return None
        d = self._post("/v2/post/publish/status/fetch/", self._token(ctx), {"publish_id": pid})
        s = d.get("status")
        if s == "FAILED":
            raise PermanentError(f"tiktok publish failed: {d.get('fail_reason')}")
        if ctx.state.get("mode") == "inbox" and s in ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"):
            if s == "SEND_TO_USER_INBOX":
                raise ManualRequired("draft sent to the creator's TikTok inbox — finish posting in the app")
        if s == "PUBLISH_COMPLETE":
            ids = d.get("publicaly_available_post_id") or d.get("publicly_available_post_id") or []
            post = str(ids[0]) if ids else pid
            user = (ctx.account.get("capabilities") or {}).get("creator_username")
            link = f"https://www.tiktok.com/@{user}/video/{post}" if ids and user else None
            return PublishOutcome(external_id=post, permalink=link, raw=d)
        return None
