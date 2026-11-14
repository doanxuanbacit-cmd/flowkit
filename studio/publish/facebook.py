"""Facebook Page Reels publisher — Meta Graph API Reels Publishing flow.

1. POST /{page-id}/video_reels upload_phase=start        -> video_id, upload_url
2. POST rupload.facebook.com/video-upload/{ver}/{video_id}  (Authorization: OAuth <page token>,
   offset, file_size headers; resumable from the reported offset)
3. POST /{page-id}/video_reels upload_phase=finish video_state=PUBLISHED|SCHEDULED|DRAFT
4. GET  /{video_id}?fields=status,permalink_url  until publishing_phase is complete

Credentials: {"page_id", "page_access_token"} (a Page token from OAuth with
pages_manage_posts / pages_read_engagement / pages_show_list). Page tokens
derived from a long-lived user token do not expire; Graph error 190 → re-auth.
"""
from __future__ import annotations

import os

from studio.publish.base import (
    AuthError, PermanentError, PublishContext, PublishOutcome, Publisher, RetryableError,
)

GRAPH_VERSION = os.environ.get("META_GRAPH_VERSION", "v26.0")
GRAPH = "https://graph.facebook.com"
RUPLOAD = "https://rupload.facebook.com/video-upload"


class FacebookReelsPublisher(Publisher):
    platform = "facebook"

    def _graph(self, method: str, path: str, token: str, **kw) -> dict:
        r = self.http.request(method, f"{GRAPH}/{GRAPH_VERSION}/{path}", params={"access_token": token, **kw.pop("params", {})}, **kw)
        try:
            body = r.json()
        except ValueError:
            body = {}
        err = body.get("error") if isinstance(body, dict) else None
        if err:
            code = err.get("code")
            msg = f"graph {code}: {err.get('message', '')[:240]}"
            if code in (190, 102) or err.get("type") == "OAuthException" and code == 200:
                raise AuthError(msg)
            if code in (1, 2, 4, 17, 32, 341, 368, 613) or err.get("is_transient"):
                raise RetryableError(msg, 300 if code in (4, 17, 32, 613) else None)
            raise PermanentError(msg)
        return self.raise_for(r)

    def capabilities(self, creds: dict, account: dict) -> dict:
        page = self._graph("GET", creds["page_id"], creds["page_access_token"], params={"fields": "id,name"})
        return {"reels_publish": True, "page_name": page.get("name")}

    def publish(self, ctx: PublishContext) -> PublishOutcome:
        self.preflight(ctx)
        token, page = ctx.credentials["page_access_token"], ctx.credentials["page_id"]
        size = ctx.video.stat().st_size
        st = ctx.state
        if not st.get("video_id"):
            res = self._graph("POST", f"{page}/video_reels", token, data={"upload_phase": "start"})
            ctx.checkpoint(video_id=res["video_id"], upload_url=res.get("upload_url"))
        vid = st["video_id"]
        if not st.get("uploaded"):
            offset = 0
            if st.get("upload_started"):  # resume: ask Meta how many bytes it already has
                s = self._graph("GET", vid, token, params={"fields": "status"})
                offset = int(((s.get("status") or {}).get("uploading_phase") or {}).get("bytes_transfered") or 0)
            ctx.checkpoint(upload_started=True)
            url = st.get("upload_url") or f"{RUPLOAD}/{GRAPH_VERSION}/{vid}"
            with open(ctx.video, "rb") as f:
                f.seek(offset)
                r = self.http.post(url, headers={"Authorization": f"OAuth {token}", "offset": str(offset),
                                                 "file_size": str(size), "Content-Type": "application/octet-stream"},
                                   content=iter(lambda: f.read(1 << 20), b""))
            body = self.raise_for(r)
            if body.get("success") is False:
                raise RetryableError(f"rupload not successful: {body}")
            ctx.checkpoint(uploaded=True)
        if not st.get("finished"):
            data = {"upload_phase": "finish", "video_id": vid, "description": ctx.caption[:2200]}
            if ctx.title:
                data["title"] = ctx.title[:255]
            sched = ctx.options.get("scheduled_publish_time")
            if ctx.options.get("draft"):
                data["video_state"] = "DRAFT"
            elif sched:
                data.update(video_state="SCHEDULED", scheduled_publish_time=str(int(sched)))
            else:
                data["video_state"] = "PUBLISHED"
            res = self._graph("POST", f"{page}/video_reels", token, data=data)
            if res.get("success") is False:
                raise PermanentError(f"finish rejected: {res}")
            ctx.checkpoint(finished=True)
        return self.check_status(ctx) or PublishOutcome(external_id=vid, status="PROCESSING")

    def check_status(self, ctx: PublishContext) -> PublishOutcome | None:
        vid = ctx.state.get("video_id")
        if not vid:
            return None
        s = self._graph("GET", vid, ctx.credentials["page_access_token"], params={"fields": "status,permalink_url"})
        status = s.get("status") or {}
        for phase in ("uploading_phase", "processing_phase", "publishing_phase"):
            ph = status.get(phase) or {}
            if ph.get("status") == "error":
                errs = ph.get("errors") or [{}]
                raise PermanentError(f"{phase}: {errs[0].get('message', 'error')}")
        pub = (status.get("publishing_phase") or {}).get("status")
        link = s.get("permalink_url")
        if link and link.startswith("/"):
            link = "https://www.facebook.com" + link
        if pub == "complete" or status.get("video_status") == "ready" and pub in (None, "complete"):
            return PublishOutcome(external_id=vid, permalink=link, raw=status)
        if status.get("video_status") == "expired":
            raise PermanentError("upload expired; re-upload required")
        return None
