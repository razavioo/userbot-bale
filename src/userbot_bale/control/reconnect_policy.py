"""Shared reconnect policy constants for all userbot-bale clients.

Single source of truth so Android, macOS, PyQt, and CLI agree on
backoff, retry caps, and stable-uptime reset thresholds.

Native clients read these via `coordinator describe` at first connect or
hard-code matching constants. The Android values in BaleVpnService.kt
(RECONNECT_BASE_DELAY_MS, RECONNECT_MAX_DELAY_MS, RECONNECT_STABLE_RESET_MS)
must match this module.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ReconnectPolicy:
    base_delay_ms: int = 3_000
    max_delay_ms: int = 30_000
    stable_reset_ms: int = 60_000
    max_attempts: int = 0  # 0 = unlimited

    def delay_ms_for_attempt(self, attempt: int) -> int:
        """Exponential backoff capped at max_delay_ms."""
        if attempt < 1:
            return self.base_delay_ms
        shift = min(attempt - 1, 4)
        return min(self.max_delay_ms, self.base_delay_ms * (1 << shift))

    def to_dict(self) -> dict:
        return asdict(self)


DEFAULT_RECONNECT_POLICY = ReconnectPolicy()
