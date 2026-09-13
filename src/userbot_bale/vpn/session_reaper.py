"""Cleanup of dead VPN sessions on the mesh relay.

Originally a closure inside `cmd_vpn_mesh`. Extracted so the
one-per-cycle invariant (which guards the shared livekit-rtc FFI
singleton from concurrent room.disconnect calls) can be unit-tested.

Cycle invariant (commit 9fea7eb): reap AT MOST ONE dead session per
cycle. Earlier the reaper processed all terminal sessions in one batch,
calling `sess.stop()` (→ `room.disconnect()`) back-to-back. With two
devices dropping within ~10 s of each other, two `room.disconnect()`
calls overlapped through the shared livekit-rtc FFI singleton and put
the FFI's internal asyncio loop into a state where every subsequent
event raised "Event loop is closed". From that point on the relay
process was effectively dead — no probe could ACK an EXPECT_CLIENT and
every dispatch failed until systemd restarted the service.

FAILED-state skip (also 9fea7eb): a session in FAILED state has
already closed its own asyncio loop. Calling stop() again would just
join the already-dead thread, but the act of running
`room.disconnect()` from a *concurrent* reap is what poisons the FFI
singleton. STOPPED state means someone already called stop() cleanly
so this is a no-op anyway.
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import Any, Callable, Protocol

from userbot_bale.runtime.metrics import counter


log = logging.getLogger(__name__)


_REAPED = counter(
    "userbot_bale_relay_reaper_reaped_total",
    "Sessions removed by the reaper, labelled by terminal state.",
    labelnames=("failed",),
)


class _MeshLike(Protocol):
    def drop_client(self, peer_id: int) -> Any: ...


class _ControlLike(Protocol):
    def release_mesh_assignment(self, peer_id: int) -> Any: ...


class _AllocatorLike(Protocol):
    def leave(self, peer_id: int) -> Any: ...


class SessionReaper:
    """Reaps terminal LiveKit sessions one-per-cycle.

    `sessions` is the live list shared with `cmd_vpn_mesh` — the reaper
    mutates it in place under no lock because the cli closure does not
    hold one either; the original implementation relied on the GIL to
    keep `sessions.append` / `sessions.pop` atomic with respect to each
    other. That assumption is preserved here.
    """

    def __init__(
        self,
        *,
        sessions: list[tuple[int, Any, Any]],
        mesh: _MeshLike,
        control: _ControlLike,
        allocator: _AllocatorLike,
        cycle_seconds: float = 2.0,
        log_stream=sys.stderr,
    ) -> None:
        self._sessions = sessions
        self._mesh = mesh
        self._control = control
        self._allocator = allocator
        self._cycle_seconds = cycle_seconds
        self._log = log_stream
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="vpn-mesh-reaper", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._cycle_seconds + 1.0)

    # ---- internals ---------------------------------------------------

    def _run(self) -> None:
        while not self._stop.wait(self._cycle_seconds):
            self.tick()

    def tick(self) -> bool:
        """Find the first terminal session and reap it. Returns True if
        a session was reaped, False if no terminal session was found.

        Test-only entrypoint; production calls `_run()` which invokes
        `tick()` on each cycle. Splitting them out lets tests verify the
        one-per-cycle invariant without spinning a real thread or sleep.
        """
        dead_index, dead_was_failed = self._find_one_dead()
        if dead_index is None:
            return False

        pid, sess, _tx = self._sessions.pop(dead_index)
        print(
            f"[vpn-mesh] reaper: cleaning up dead session peer={pid} "
            f"(failed={dead_was_failed})",
            file=self._log,
        )
        # Skip stop() for FAILED — see module docstring.
        if not dead_was_failed:
            try:
                sess.stop()
            except Exception:  # noqa: BLE001
                pass
        try:
            self._mesh.drop_client(pid)  # invokes on_drop → clears _account_active
        except Exception:  # noqa: BLE001
            log.exception("reaper: drop_client failed for peer=%d", pid)
        try:
            self._control.release_mesh_assignment(pid)
        except Exception:  # noqa: BLE001
            log.exception("reaper: release_mesh_assignment failed for peer=%d", pid)
        try:
            self._allocator.leave(pid)
        except Exception:  # noqa: BLE001
            pass
        try:
            _REAPED.inc(failed=str(dead_was_failed))
        except Exception:
            pass
        return True

    def _find_one_dead(self) -> tuple[int | None, bool]:
        """Locate the first terminal session in the list. Returns
        (index, was_failed) or (None, False) if nothing to reap."""
        for i, (_pid, sess, _tx) in enumerate(self._sessions):
            try:
                if sess.is_terminal():
                    was_failed = False
                    try:
                        from userbot_bale.bale.livekit_backend import (
                            LiveKitSessionState,
                        )
                        was_failed = (
                            getattr(sess, "_state", None)
                            == LiveKitSessionState.FAILED
                        )
                    except Exception:  # noqa: BLE001
                        was_failed = False
                    return i, was_failed
            except Exception:  # noqa: BLE001
                # Probing the session itself raised — treat as terminal
                # AND failed so we don't try to call stop() on it.
                return i, True
        return None, False
