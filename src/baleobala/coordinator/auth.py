"""Relay-to-coordinator authentication (A6).

Each relay is enrolled with a per-relay HMAC-SHA256 secret (32 random
bytes, hex-encoded). The relay includes a short `sig` field in every
control message it sends (ONLINE / HEARTBEAT / OFFLINE / RELEASED). The
coordinator verifies the sig before acting on the message.

Signature scheme:
    sig = HMAC-SHA256(secret_bytes, f"{relay_id}:{kind}:{epoch_minute}")
          .hexdigest()[:16]

`epoch_minute = int(time.time() // 60)` so the window is ±60 s (the
coordinator accepts the current minute and the previous one). Short enough
to limit replay; long enough to absorb clock skew between relay and
coordinator hosts.

The coordinator only enforces auth for relay_ids whose secret is stored in
the registry (opt-in). Relays without a registered secret are accepted as
before (backwards-compat for operators who haven't run `enroll` yet); a
WARNING is logged so the gap is visible in the journal.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time


def generate_secret() -> str:
    """Return a new random relay secret as 64-char hex (32 bytes)."""
    return os.urandom(32).hex()


def _epoch_minute(ts: float | None = None) -> int:
    return int((ts if ts is not None else time.time()) // 60)


def sign(secret_hex: str, relay_id: str, kind: str, *, ts: float | None = None) -> str:
    """Compute a 16-char hex HMAC signature for a relay control message."""
    key = bytes.fromhex(secret_hex)
    minute = _epoch_minute(ts)
    msg = f"{relay_id}:{kind}:{minute}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:16]


def verify(
    secret_hex: str,
    relay_id: str,
    kind: str,
    sig: str,
    *,
    ts: float | None = None,
) -> bool:
    """Return True if `sig` matches the current or previous minute's signature.

    Accepts t and t-1 to tolerate ≤60 s clock skew between hosts.
    """
    if not sig or len(sig) != 16:
        return False
    now = ts if ts is not None else time.time()
    for minute_offset in (0, -1):
        minute = _epoch_minute(now) + minute_offset
        key = bytes.fromhex(secret_hex)
        msg = f"{relay_id}:{kind}:{minute}".encode()
        expected = hmac.new(key, msg, hashlib.sha256).hexdigest()[:16]
        if hmac.compare_digest(expected, sig):
            return True
    return False
