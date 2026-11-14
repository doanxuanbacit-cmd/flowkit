"""Publisher contract shared by the four platform connectors.

A publisher is a small resumable state machine: `publish()` reads/writes
`ctx.state` (persisted in publish_job.state after every step), so a retry after
a crash or timeout continues from the last completed step instead of uploading
the same video twice. Platforms without an idempotency key (Meta, TikTok) are
protected this way plus the job-level idempotency key.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx


class PublishError(Exception):
    pass


class RetryableError(PublishError):
    def __init__(self, msg: str, retry_after: float | None = None):
        super().__init__(msg)
        self.retry_after = retry_after


class PermanentError(PublishError):
    pass


class AuthError(PublishError):
    """Token revoked/expired and not refreshable — account needs re-auth."""


class ManualRequired(PublishError):
    """The API (or the permissions granted) cannot publish this; a human must."""


@dataclass
class PublishContext:
    job_id: str
    video: Path
    thumbnail: Path | None
    title: str
    caption: str
    tags: list[str]
    privacy: str
    options: dict
    credentials: dict
    account: dict
    state: dict = field(default_factory=dict)
    save_state: Callable[[dict], None] = lambda s: None
    save_credentials: Callable[[dict], None] = lambda c: None

    def checkpoint(self, **kv) -> None:
        self.state.update(kv)
        self.save_state(self.state)


@dataclass
class PublishOutcome:
    external_id: str
    permalink: str | None = None
    status: str = "PUBLISHED"     # PUBLISHED | PROCESSING (platform still processing)
    raw: dict = field(default_factory=dict)


class Publisher:
    platform: str = ""
    max_bytes: int | None = None

    def __init__(self, client: httpx.Client | None = None):
        self.http = client or httpx.Client(timeout=httpx.Timeout(60.0, read=300.0))

    def publish(self, ctx: PublishContext) -> PublishOutcome:  # pragma: no cover - abstract
        raise NotImplementedError

    def check_status(self, ctx: PublishContext) -> PublishOutcome | None:
        """Poll a PROCESSING upload; None = still processing."""
        return None

    def capabilities(self, creds: dict, account: dict) -> dict:
        """Verify what this token may actually do (called when connecting)."""
        return {}

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def raise_for(resp: httpx.Response, auth_codes: tuple = (401,)) -> dict:
        try:
            body = resp.json()
        except (ValueError, json.JSONDecodeError):
            body = {"text": resp.text[:500]}
        if resp.status_code in auth_codes:
            raise AuthError(f"{resp.status_code}: {str(body)[:300]}")
        if resp.status_code == 429 or resp.status_code >= 500:
            ra = resp.headers.get("retry-after")
            raise RetryableError(f"{resp.status_code}: {str(body)[:300]}",
                                 float(ra) if ra and ra.isdigit() else None)
        if resp.status_code >= 400:
            raise PermanentError(f"{resp.status_code}: {str(body)[:300]}")
        return body

    def preflight(self, ctx: PublishContext) -> None:
        if not ctx.video.exists():
            raise PermanentError(f"rendition missing: {ctx.video}")
        if self.max_bytes and ctx.video.stat().st_size > self.max_bytes:
            raise ManualRequired(f"file {ctx.video.stat().st_size} bytes exceeds {self.platform} API limit "
                                 f"{self.max_bytes}")
