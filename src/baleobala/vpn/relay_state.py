"""Per-account probe-active flag tracking for the VPN relay.

Originally a closure-shared pair of dicts plus a `_set_active` helper
inside `cmd_vpn_mesh`. Extracted so the relay handler can be unit-tested
without driving the whole CLI; behavior is byte-equivalent to the
original closure, including the stderr audit log that production
incident triage relies on.

The flag protects the relay against a duplicate Bale push (the SFU
sometimes redelivers a call notification after AcceptCall) starting a
second probe that would overwrite the live tunnel via mesh.issue_client.
A second push arriving within the ~5 s window before allocator.join
populates the slot would otherwise replace the live transport with one
bound to a different LiveKit room, silently dropping every Android frame
→ tunnel_dead in 6 s.

Stale-flag auto-recovery: in practice we still see leaks (probe.start()
hanging, daemon thread killed mid-call, untracked code paths). When the
flag is found stuck True with no slot allocated and the age exceeds
the configured threshold, the next inbound call force-clears it so a
legitimate connection isn't blocked until the systemd RuntimeMaxSec
restart cycle (55-70 min).
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Callable

from baleobala.runtime.metrics import counter, gauge


_TRANSITIONS = counter(
    "baleobala_relay_active_transitions_total",
    "Per-account active-flag transitions, labelled by destination value and call site.",
    labelnames=("account", "value", "where"),
)
_ACTIVE_AGE = gauge(
    "baleobala_relay_active_flag_age_seconds",
    "Seconds since the per-account active flag was set True; 0 when the flag is False. "
    "Alert when this exceeds STALE_ACTIVE_FLAG_SECS to catch leaks.",
    labelnames=("account",),
)


# Default age threshold beyond which a True flag with no allocated slot
# is treated as leaked from a prior code path and force-cleared.
DEFAULT_STALE_ACTIVE_FLAG_SECS = 60.0


class RelayState:
    """Per-account active-probe flag with audit-logged transitions."""

    def __init__(
        self,
        *,
        num_accounts: int,
        clock: Callable[[], float] = time.time,
        log_stream=sys.stderr,
    ) -> None:
        self._active: dict[int, bool] = {i: False for i in range(num_accounts)}
        self._active_at: dict[int, float] = {i: 0.0 for i in range(num_accounts)}
        self._clock = clock
        self._log = log_stream
        # Per-scrape gauge: register one callback per account so the
        # gauge surfaces the *current* age at scrape time, not the age
        # at last transition. This is the signal that catches leaked
        # flags before they trip the auto-recovery path.
        for idx in range(num_accounts):
            _ACTIVE_AGE.set_function(
                (lambda i=idx: self.age(i)), account=str(idx),
            )
        # Transitions can be invoked from the listen-thread, the worker
        # thread that runs on_incoming_call, the reaper thread, and the
        # mesh.drop_client on_drop callback (which is itself thread-safe
        # but called from the LiveKit FFI thread). A simple lock keeps
        # the state + the timestamp + the audit log line atomic.
        self._lock = threading.Lock()

    def is_active(self, idx: int) -> bool:
        with self._lock:
            return bool(self._active.get(idx, False))

    def active_at(self, idx: int) -> float:
        with self._lock:
            return float(self._active_at.get(idx, 0.0))

    def age(self, idx: int) -> float:
        """Wall-clock seconds since this flag was set True. Returns 0.0
        when the flag is currently False (matches the original behavior
        where the timestamp was reset to 0.0 at False transitions)."""
        with self._lock:
            if not self._active.get(idx, False):
                return 0.0
            return max(0.0, self._clock() - self._active_at.get(idx, 0.0))

    def is_stale(self, idx: int, *, threshold: float = DEFAULT_STALE_ACTIVE_FLAG_SECS) -> bool:
        """True when the flag has been set longer than `threshold` and is
        therefore a candidate for force-clearing on a fresh inbound call.
        Callers must independently check that no slot is allocated for
        this account before treating staleness as a leak."""
        with self._lock:
            if not self._active.get(idx, False):
                return False
            age = self._clock() - self._active_at.get(idx, 0.0)
            return age > threshold

    def transition(self, idx: int, value: bool, where: str) -> None:
        """Set the flag, update the timestamp, and emit the audit log
        line that the original `_set_active` produced. The line format
        is preserved verbatim because production triage greps for it."""
        with self._lock:
            prev = self._active.get(idx)
            self._active[idx] = value
            if value:
                self._active_at[idx] = self._clock()
            else:
                self._active_at[idx] = 0.0
            if prev != value:
                # Reproduces the original print exactly so journalctl
                # greps continue to match. The thread name is captured
                # outside the lock-held branch wouldn't matter — it's a
                # cheap call.
                msg = (
                    f"[vpn-mesh] _active[{idx}]: {prev}→{value} at {where} "
                    f"(thread={threading.current_thread().name})"
                )
            else:
                msg = None
        if msg is not None:
            print(msg, file=self._log)
            # Only count *real* transitions (prev != value); same-value
            # writes are no-ops in production and shouldn't inflate the
            # counter.
            try:
                _TRANSITIONS.inc(account=str(idx), value=str(value), where=where)
            except Exception:
                # Metrics must never break the relay path.
                pass
