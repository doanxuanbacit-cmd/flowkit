"""Credential encryption at rest (Fernet / AES-128-CBC + HMAC-SHA256).

The key comes from STUDIO_SECRET_KEY only — it is never stored next to the
data. Generate one with:  python -m studio.publish.crypto
"""
from __future__ import annotations

import json

from cryptography.fernet import Fernet, InvalidToken

from studio import config


class CryptoError(RuntimeError):
    pass


def _fernet(key: str | None = None) -> Fernet:
    k = key if key is not None else config.SECRET_KEY
    if not k:
        raise CryptoError("STUDIO_SECRET_KEY is not set; refusing to store credentials")
    try:
        return Fernet(k.encode() if isinstance(k, str) else k)
    except (ValueError, TypeError) as e:
        raise CryptoError("STUDIO_SECRET_KEY is not a valid Fernet key") from e


def encrypt_json(data: dict, key: str | None = None) -> str:
    return _fernet(key).encrypt(json.dumps(data).encode()).decode()


def decrypt_json(token: str, key: str | None = None) -> dict:
    try:
        return json.loads(_fernet(key).decrypt(token.encode()))
    except InvalidToken as e:
        raise CryptoError("credentials cannot be decrypted with the current key") from e


def redact(d: dict) -> dict:
    """Safe-to-log/return view of a credentials dict."""
    return {k: ("***" if any(s in k.lower() for s in ("token", "secret", "key", "password")) else v)
            for k, v in d.items()}


if __name__ == "__main__":
    print(Fernet.generate_key().decode())
