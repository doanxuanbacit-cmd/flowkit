"""Zalo OA publisher — OA OpenAPI v2.0 article endpoints (video article).

What the OA API offers is *articles* (normal or video type) — not a Reels-like
feed. The flow used here:
1. POST /v2.0/article/upload_video/preparevideo   (multipart `file`; MP4/AVI ≤ 50 MB) -> token
2. GET  /v2.0/article/upload_video/verify?token=   until status==1 (SUCCESS) -> video_id
3. POST /v2.0/article/create  {type:"video", title, description, video_id, avatar, status:"show"} -> token
4. GET  /v2.0/article/verify?token=               -> article id

All calls send the OA token in the `access_token` header. These endpoints and
their permission (`manage_article`) must be enabled for the OA/app; capability
probing runs when the account is connected, and anything the token cannot do
falls back to MANUAL_REQUIRED with a ready-to-post package for OA Manager.
Credentials: {"access_token", "refresh_token", "app_id", "secret_key", "expires_at"}.
"""
from __future__ import annotations

import os
import time

from studio.publish.base import (
    AuthError, ManualRequired, PermanentError, PublishContext, PublishOutcome, Publisher, RetryableError,
)

API = os.environ.get("ZALO_API_BASE", "https://openapi.zalo.me")
OAUTH = "https://oauth.zaloapp.com/v4/oa/access_token"
MAX_VIDEO_BYTES = 50 * 1024 * 1024
AUTH_ERRORS = {-216, -124, -230}          # invalid / expired access token
PERMISSION_ERRORS = {-224, -201, -221, -240}  # no permission / feature not enabled for the OA


class ZaloOAPublisher(Publisher):
    platform = "zalo"
    max_bytes = MAX_VIDEO_BYTES

    def _parse(self, r) -> dict:
        body = self.raise_for(r)
        err = body.get("error", 0)
        if err not in (0, None):
            msg = f"zalo {err}: {body.get('message', '')[:240]}"
            if err in AUTH_ERRORS:
                raise AuthError(msg)
            if err in PERMISSION_ERRORS:
                raise ManualRequired(msg)
            if err in (-32, -50):      # rate limit / system busy
                raise RetryableError(msg, 120)
            raise PermanentError(msg)
        return body.get("data") or {}

    def refresh(self, creds: dict) -> dict:
        r = self.http.post(OAUTH, headers={"secret_key": creds["secret_key"]},
                           data={"app_id": creds["app_id"], "grant_type": "refresh_token",
                                 "refresh_token": creds["refresh_token"]})
        body = self.raise_for(r)
        if "access_token" not in body:
            raise AuthError(f"zalo refresh failed: {body.get('error_description') or body}")
        return {**creds, "access_token": body["access_token"], "refresh_token": body.get("refresh_token"),
                "expires_at": time.time() + float(body.get("expires_in", 90000))}

    def _token(self, ctx: PublishContext) -> str:
        c = ctx.credentials
        if c.get("refresh_token") and float(c.get("expires_at", 0)) - time.time() < 600:
            ctx.credentials = self.refresh(c)
            ctx.save_credentials(ctx.credentials)
        return ctx.credentials["access_token"]

    def capabilities(self, creds: dict, account: dict) -> dict:
        r = self.http.get(f"{API}/v2.0/article/getslice", headers={"access_token": creds["access_token"]},
                          params={"offset": 0, "limit": 1, "type": "video"})
        try:
            self._parse(r)
            return {"article_api": True}
        except ManualRequired as e:
            return {"article_api": False, "reason": str(e)}

    def publish(self, ctx: PublishContext) -> PublishOutcome:
        caps = ctx.account.get("capabilities") or {}
        if caps.get("article_api") is False:
            raise ManualRequired(f"OA article API not permitted: {caps.get('reason', 'not enabled')}")
        self.preflight(ctx)
        tok = self._token(ctx)
        h = {"access_token": tok}
        st = ctx.state
        if not st.get("video_token") and not st.get("video_id"):
            with open(ctx.video, "rb") as f:
                d = self._parse(self.http.post(f"{API}/v2.0/article/upload_video/preparevideo", headers=h,
                                               files={"file": (ctx.video.name, f, "video/mp4")}))
            ctx.checkpoint(video_token=d["token"])
        if not st.get("video_id"):
            d = self._parse(self.http.get(f"{API}/v2.0/article/upload_video/verify", headers=h,
                                          params={"token": st["video_token"]}))
            status = int(d.get("status", 0))
            if status in (0, 3):
                raise RetryableError(f"zalo video processing ({d.get('convert_percent', 0)}%)", 15)
            if status != 1:
                raise PermanentError(f"zalo video status {status} ({d.get('status_message', '')})")
            ctx.checkpoint(video_id=d["video_id"])
        if not st.get("article_token"):
            cover = ctx.options.get("cover_url")
            body = {"type": "video", "title": (ctx.title or ctx.caption)[:150], "description": ctx.caption[:300],
                    "video_id": st["video_id"], "status": "show", "comment": "show"}
            if cover:
                body["avatar"] = cover
            d = self._parse(self.http.post(f"{API}/v2.0/article/create", headers=h, json=body))
            ctx.checkpoint(article_token=d["token"])
        d = self._parse(self.http.get(f"{API}/v2.0/article/verify", headers=h, params={"token": st["article_token"]}))
        aid = d.get("id") or d.get("article_id")
        if not aid:
            return PublishOutcome(external_id=st["article_token"], status="PROCESSING")
        return PublishOutcome(external_id=str(aid), raw=d)

    def check_status(self, ctx: PublishContext) -> PublishOutcome | None:
        if not ctx.state.get("article_token"):
            return None
        out = self.publish(ctx)
        return out if out.status == "PUBLISHED" else None
