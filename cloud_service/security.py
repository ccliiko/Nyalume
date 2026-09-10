"""Small password and bearer-token helpers using only the standard library."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time


class TokenError(ValueError):
    pass


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32
    )
    return f"scrypt$16384$8$1${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=32,
        )
        return hmac.compare_digest(digest, _b64decode(expected))
    except (ValueError, TypeError):
        return False


def reset_code_digest(user_id: str, code: str, secret: str) -> str:
    """Bind a short reset code to one account without storing the code itself."""
    return hmac.new(
        secret.encode("utf-8"), f"{user_id}:{code}".encode("utf-8"), hashlib.sha256
    ).hexdigest()


def create_token(
    user_id: str,
    device_id: str,
    secret: str,
    ttl_seconds: int = 30 * 24 * 60 * 60,
    *,
    now: int | None = None,
) -> str:
    issued_at = int(time.time() if now is None else now)
    payload = _b64encode(
        json.dumps(
            {
                "sub": user_id,
                "device": device_id,
                "iat": issued_at,
                "exp": issued_at + ttl_seconds,
            },
            separators=(",", ":"),
        ).encode("utf-8")
    )
    signed = f"v1.{payload}"
    signature = _b64encode(
        hmac.new(secret.encode("utf-8"), signed.encode("ascii"), hashlib.sha256).digest()
    )
    return f"{signed}.{signature}"


def read_token(token: str, secret: str, *, now: int | None = None) -> dict:
    try:
        version, payload, signature = token.split(".", 2)
        signed = f"{version}.{payload}"
        expected = hmac.new(
            secret.encode("utf-8"), signed.encode("ascii"), hashlib.sha256
        ).digest()
        if version != "v1" or not hmac.compare_digest(expected, _b64decode(signature)):
            raise TokenError("invalid token")
        claims = json.loads(_b64decode(payload))
        current = int(time.time() if now is None else now)
        if int(claims["exp"]) <= current or not claims.get("sub") or not claims.get("device"):
            raise TokenError("expired token")
        return claims
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        if isinstance(exc, TokenError):
            raise
        raise TokenError("invalid token") from exc
