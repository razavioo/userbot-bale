"""
Small helper for inspecting a Bale JWT without validating its signature.

The token's `exp` field is useful to warn operators before the tunnel
fails at 3 AM. We don't refresh automatically — Bale's tokens last
~a year and refresh requires another round of phone-SMS auth (which
isn't implemented in the repo yet). We just surface the expiry.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class JwtInfo:
    exp: Optional[int]
    iat: Optional[int]
    seconds_until_expiry: Optional[float]


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def inspect(jwt: str) -> JwtInfo:
    """Parse claims; no signature check."""
    parts = jwt.split(".")
    if len(parts) < 2:
        return JwtInfo(exp=None, iat=None, seconds_until_expiry=None)
    try:
        claims = json.loads(_b64url_decode(parts[1]))
    except (ValueError, json.JSONDecodeError):
        return JwtInfo(exp=None, iat=None, seconds_until_expiry=None)
    exp = claims.get("exp")
    iat = claims.get("iat")
    ttl = (exp - time.time()) if isinstance(exp, (int, float)) else None
    return JwtInfo(exp=exp, iat=iat, seconds_until_expiry=ttl)


class JwtExpiredError(RuntimeError):
    """Raised when a Bale JWT has already expired (or is about to)."""

    def __init__(self, message: str, *, exp: Optional[int]) -> None:
        super().__init__(message)
        self.exp = exp


def is_expired(jwt: str, *, skew_seconds: float = 0.0) -> bool:
    """Return True if the JWT's `exp` claim is in the past (with optional
    safety skew). Tokens without a parseable `exp` are treated as
    non-expired — Bale will still reject them at WS upgrade time, and we
    don't want to block users on tokens we can't decode."""
    info = inspect(jwt)
    if info.seconds_until_expiry is None:
        return False
    return info.seconds_until_expiry <= skew_seconds


def require_unexpired(jwt: str, *, skew_seconds: float = 60.0) -> None:
    """Raise JwtExpiredError if the JWT is already expired or expires
    within `skew_seconds`. Default skew is 60s so we don't race the
    server's clock on a token that will die mid-handshake."""
    info = inspect(jwt)
    if info.seconds_until_expiry is None:
        return
    if info.seconds_until_expiry <= skew_seconds:
        raise JwtExpiredError(
            f"Bale JWT expired or expires within {skew_seconds:.0f}s "
            f"(exp={info.exp}, ttl={info.seconds_until_expiry:.0f}s). "
            "Re-authenticate via the login flow.",
            exp=info.exp,
        )


def warn_if_near_expiry(jwt: str, *, days: float = 14.0) -> None:
    info = inspect(jwt)
    if info.seconds_until_expiry is None:
        return
    if info.seconds_until_expiry < 0:
        log.error(
            "JWT is EXPIRED (exp=%s). Bale will refuse the WS connection.",
            info.exp,
        )
        return
    if info.seconds_until_expiry < days * 86400:
        log.warning(
            "JWT expires in %.1f days (exp=%s). Capture a fresh one soon.",
            info.seconds_until_expiry / 86400, info.exp,
        )
