"""
Failover router for transports. Tries an ordered list of factories and
returns the first one that constructs successfully. This is the
top-level knob the CLI exposes as `--transport auto`.

Preference order (tune per-deployment):
    1. DataChannel  — reliable, ordered, ~100 KB/s.
    2. Audio        — universal last-resort if DC is blocked.
    3. RPC          — store-and-forward, high latency.
    4. Video-QR     — currently a skeleton; skipped unless explicitly
                      enabled.

Mid-session swap (tear down one transport and resume on another without
dropping TCP sessions) requires the tunnel core to preserve its seq
space across swaps. Not wired yet — see the plan file. For now this
router selects once at startup; if the call drops you restart.

A `HealthMonitor` thread logs when the tunnel's ARQ queue is stuck
(many in-flight frames without ACKs) — an operator signal that a swap
would help.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

log = logging.getLogger(__name__)


TransportFactory = Callable[[], object]  # returns a Transport


@dataclass
class RouterChoice:
    name: str
    factory: TransportFactory


class FailoverRouter:
    """
    Pick the first transport that builds. `build()` returns (name, transport)
    or raises RuntimeError if nothing works.
    """

    def __init__(self, choices: List[RouterChoice]) -> None:
        if not choices:
            raise ValueError("at least one RouterChoice required")
        self._choices = choices

    def build(self):  # type: ignore[no-untyped-def]
        errors: list[tuple[str, Exception]] = []
        for c in self._choices:
            try:
                log.info("router: attempting transport %s", c.name)
                transport = c.factory()
                log.info("router: using transport %s (mtu=%d)",
                         c.name, getattr(transport, "mtu", -1))
                return c.name, transport
            except Exception as e:  # noqa: BLE001
                log.warning("router: transport %s failed: %s", c.name, e)
                errors.append((c.name, e))
        detail = "; ".join(f"{n}: {e}" for n, e in errors)
        raise RuntimeError(f"no transport available — {detail}")


class TransportChain:
    """An ordered list of transport factories with a cursor. `current()`
    returns the live transport; `advance()` builds the next one, closes
    the previous, and returns it so the caller can hand it to
    `Tunnel.swap_transport`. Exhaustion raises.

    This is the mid-session companion to FailoverRouter's build-once
    selection: the router picks where to start, the chain lets us walk
    to the next option when the current one dies."""

    def __init__(self, choices: List[RouterChoice]) -> None:
        if not choices:
            raise ValueError("at least one RouterChoice required")
        self._choices = choices
        self._idx = -1
        self._current: object | None = None
        self._current_name: str | None = None

    def current(self):  # type: ignore[no-untyped-def]
        if self._current is None:
            raise RuntimeError("TransportChain not started")
        return self._current_name, self._current

    def advance(self):  # type: ignore[no-untyped-def]
        """Build the next transport in order. On success, returns the
        new (name, transport) and stashes the old one as `last_closed`.
        If every subsequent candidate fails, raises RuntimeError; the
        chain is left pointing at the last good transport."""
        saved_idx = self._idx
        old = self._current
        old_name = self._current_name
        errors: list[tuple[str, Exception]] = []
        i = self._idx + 1
        while i < len(self._choices):
            c = self._choices[i]
            try:
                log.info("chain: advancing to transport %s", c.name)
                new = c.factory()
                self._idx = i
                self._current = new
                self._current_name = c.name
                if old is not None:
                    try:
                        old.close()  # type: ignore[attr-defined]
                    except Exception:  # noqa: BLE001
                        log.exception("closing old transport %s failed", old_name)
                return c.name, new
            except Exception as e:  # noqa: BLE001
                log.warning("chain: transport %s failed: %s", c.name, e)
                errors.append((c.name, e))
                i += 1
        # No usable next transport. Leave state unchanged.
        self._idx = saved_idx
        detail = "; ".join(f"{n}: {e}" for n, e in errors)
        raise RuntimeError(f"no further transport available — {detail}")

    def start(self):  # type: ignore[no-untyped-def]
        """First activation — equivalent to advancing from before-start."""
        return self.advance()

    def close(self) -> None:
        if self._current is not None:
            try:
                self._current.close()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
            self._current = None
            self._current_name = None


class HealthMonitor:
    """Log when a tunnel's in-flight ARQ queue grows without draining.

    This is a lightweight observer; it doesn't swap transports. Call
    `start()` after the tunnel is running; `stop()` on shutdown.
    """

    def __init__(self, tunnel, *, interval: float = 5.0,
                 stuck_threshold: int = 8) -> None:  # type: ignore[no-untyped-def]
        self._tunnel = tunnel
        self._interval = interval
        self._threshold = stuck_threshold
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="vpn-health", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _run(self) -> None:
        last_pending = 0
        while not self._stop.wait(self._interval):
            # Reach into the tunnel's pending-ACK table; it's internal
            # but this is the operational signal we need.
            with getattr(self._tunnel, "_pending_lock", threading.Lock()):
                pending = len(getattr(self._tunnel, "_pending", {}))
            if pending >= self._threshold and pending == last_pending:
                log.warning(
                    "vpn health: %d frames unacked for >%.1fs — transport "
                    "may be stuck. Consider restarting with a different "
                    "--transport.",
                    pending, self._interval,
                )
            last_pending = pending
