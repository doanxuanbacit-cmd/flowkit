"""Studio V4 configuration. Everything is overridable by environment variable."""
import os
from pathlib import Path

from agent.config import BASE_DIR, DB_PATH as FLOWKIT_DB_PATH, OUTPUT_DIR  # noqa: F401  (re-exported)

STUDIO_DIR = Path(os.environ.get("STUDIO_DIR", BASE_DIR / "studio_data"))
STUDIO_DB = Path(os.environ.get("STUDIO_DB", STUDIO_DIR / "studio.db"))
ASSETS_DIR = STUDIO_DIR / "assets"
RENDERS_DIR = STUDIO_DIR / "renders"
CACHE_DIR = STUDIO_DIR / "cache"

# Credential encryption key (Fernet, urlsafe base64 32 bytes). Required before
# any social account is connected; never logged, never stored in the DB.
SECRET_KEY = os.environ.get("STUDIO_SECRET_KEY", "")

RENDER_WORKERS = max(1, int(os.environ.get("STUDIO_RENDER_WORKERS", "1")))
PUBLISH_POLL_S = float(os.environ.get("STUDIO_PUBLISH_POLL_S", "15"))
PUBLISH_MAX_ATTEMPTS = int(os.environ.get("STUDIO_PUBLISH_MAX_ATTEMPTS", "5"))
# Approval gate: renders must pass QA and be approved before any publish.
REQUIRE_APPROVAL = os.environ.get("STUDIO_REQUIRE_APPROVAL", "1") != "0"
ALLOW_REMOTE_DOWNLOAD = os.environ.get("STUDIO_ALLOW_REMOTE_DOWNLOAD", "1") == "1"
MAX_UPLOAD_MB = int(os.environ.get("STUDIO_MAX_UPLOAD_MB", "2048"))


def ensure_dirs() -> None:
    for d in (STUDIO_DIR, ASSETS_DIR, RENDERS_DIR, CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)
