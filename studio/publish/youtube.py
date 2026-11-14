"""YouTube Shorts publisher — YouTube Data API v3 resumable upload.

Raw HTTP (no google client discovery) so the flow is testable and resumable:
1. POST /upload/youtube/v3/videos?uploadType=resumable&part=snippet,status  -> Location (session URI)
2. PUT session URI with the bytes (on interruption, PUT with Content-Range: bytes */size to
   learn the received range and resume)
3. POST /upload/youtube/v3/thumbnails/set?videoId=  (optional; needs a verified channel)

Scheduling uses status.privacyStatus=private + status.publishAt (RFC 3339).
quotaExceeded (403) is retried after the daily reset (midnight Pacific).
Credentials: {"refresh_token", "client_id", "client_secret", "access_token"?, "expires_at"?}
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from studio.publish.base import (
    AuthError, PermanentError, PublishContext, PublishOutcome, Publisher, RetryableError,
)

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3"
API = "https://www.googleapis.com/youtube/v3"
CHUNK = 8 * 256 * 1024 * 4   # 8 MiB, multiple of 256 KiB as the API requires


def seconds_to_quota_reset(now: datetime | None = None) -> float:
    pt = ZoneInfo("America/Los_Angeles")
    n = (now or datetime.now(timezone.utc)).astimezone(pt)
    nxt = (n + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    return (nxt - n).total_seconds()


class YouTubeShortsPublisher(Publisher):
    platform = "youtube"

    def _token(self, ctx: PublishContext) -> str:
        c = ctx.credentials
        if c.get("access_token") and float(c.get("expires_at", 0)) - time.time() > 120:
            return c["access_token"]
        r = self.http.post(TOKEN_URL, data={"client_id": c["client_id"], "client_secret": c["client_secret"],
                                            "refresh_token": c["refresh_token"], "grant_type": "refresh_token"})
        if r.status_code in (400, 401):
            raise AuthError(f"google token refresh failed: {r.text[:200]}")
        body = self.raise_for(r)
        ctx.credentials = {**c, "access_token": body["access_token"],
                           "expires_at": time.time() + float(body.get("expires_in", 3600))}
        ctx.save_credentials(ctx.credentials)
        return ctx.credentials["access_token"]

    def _check(self, r) -> dict:
        if r.status_code == 403:
            try:
                reasons = {e.get("reason") for e in r.json()["error"]["errors"]}
            except (ValueError, KeyError, TypeError):
                reasons = set()
            if reasons & {"quotaExceeded", "dailyLimitExceeded"}:
                raise RetryableError("YouTube quota exceeded", seconds_to_quota_reset())
            if reasons & {"rateLimitExceeded", "userRateLimitExceeded"}:
                raise RetryableError("YouTube rate limited", 120)
            if reasons & {"uploadLimitExceeded"}:
                raise RetryableError("channel upload limit reached", 6 * 3600)
            raise PermanentError(f"403: {r.text[:300]}")
        return self.raise_for(r)

    def capabilities(self, creds: dict, account: dict) -> dict:
        ctx = PublishContext(job_id="cap", video=None, thumbnail=None, title="", caption="", tags=[], privacy="",
                             options={}, credentials=creds, account=account)
        r = self.http.get(f"{API}/channels", params={"part": "snippet,status", "mine": "true"},
                          headers={"Authorization": f"Bearer {self._token(ctx)}"})
        items = self._check(r).get("items") or []
        if not items:
            raise PermanentError("token has no YouTube channel")
        return {"upload": True, "channel_id": items[0]["id"], "channel_title": items[0]["snippet"]["title"],
                "long_uploads": (items[0].get("status") or {}).get("longUploadsStatus")}

    def publish(self, ctx: PublishContext) -> PublishOutcome:
        self.preflight(ctx)
        tok = self._token(ctx)
        size = ctx.video.stat().st_size
        st = ctx.state
        if not st.get("session"):
            title = ctx.title or ctx.caption[:90]
            if ctx.options.get("shorts_hashtag", True) and "#shorts" not in (title + ctx.caption).lower():
                title = f"{title} #Shorts"
            status = {"privacyStatus": ctx.privacy if ctx.privacy in ("public", "unlisted", "private") else "private",
                      "selfDeclaredMadeForKids": bool(ctx.options.get("made_for_kids", False)),
                      "containsSyntheticMedia": bool(ctx.options.get("synthetic_media", True))}
            if ctx.options.get("publish_at"):
                status.update(privacyStatus="private", publishAt=ctx.options["publish_at"])
            body = {"snippet": {"title": title[:100], "description": ctx.caption[:5000], "tags": ctx.tags[:30],
                                "categoryId": str(ctx.options.get("category_id", "22")),
                                "defaultLanguage": ctx.options.get("language", "vi")}, "status": status}
            r = self.http.post(f"{UPLOAD}/videos", params={"uploadType": "resumable", "part": "snippet,status"},
                               headers={"Authorization": f"Bearer {tok}", "X-Upload-Content-Type": "video/mp4",
                                        "X-Upload-Content-Length": str(size)}, json=body)
            self._check(r)
            ctx.checkpoint(session=r.headers["location"], offset=0)
        if not st.get("video_id"):
            offset = int(st.get("offset", 0))
            if offset:  # resume: ask the session what it has
                q = self.http.put(st["session"], headers={"Authorization": f"Bearer {tok}",
                                                         "Content-Range": f"bytes */{size}"})
                if q.status_code in (200, 201):
                    ctx.checkpoint(video_id=q.json()["id"])
                elif q.status_code == 308:
                    rng = q.headers.get("range")
                    offset = int(rng.split("-")[1]) + 1 if rng else 0
                elif q.status_code == 404:
                    ctx.state.pop("session", None)
                    ctx.checkpoint(offset=0)
                    raise RetryableError("upload session expired; restarting", 5)
                else:
                    self._check(q)
            with open(ctx.video, "rb") as f:
                while not st.get("video_id"):
                    f.seek(offset)
                    data = f.read(CHUNK)
                    end = offset + len(data) - 1
                    r = self.http.put(st["session"], content=data, headers={
                        "Authorization": f"Bearer {tok}", "Content-Length": str(len(data)),
                        "Content-Range": f"bytes {offset}-{end}/{size}"})
                    if r.status_code == 308:
                        rng = r.headers.get("range")
                        offset = int(rng.split("-")[1]) + 1 if rng else end + 1
                        ctx.checkpoint(offset=offset)
                        continue
                    body = self._check(r)
                    ctx.checkpoint(video_id=body["id"], offset=size)
        vid = st["video_id"]
        if ctx.thumbnail and ctx.thumbnail.exists() and not st.get("thumb_done"):
            r = self.http.post(f"{UPLOAD}/thumbnails/set", params={"videoId": vid},
                               headers={"Authorization": f"Bearer {tok}", "Content-Type": "image/jpeg"},
                               content=ctx.thumbnail.read_bytes())
            # custom thumbnails need a verified channel; never fail the post for it
            ctx.checkpoint(thumb_done=True, thumb_status=r.status_code)
        return PublishOutcome(external_id=vid, permalink=f"https://youtube.com/shorts/{vid}")
