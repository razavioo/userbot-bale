"""Regression tests for the relay session reaper (commit 9fea7eb).

The reaper exists to free `sessions` / allocator / mesh / control state
when a LiveKit room ends without the main loop noticing. The
one-per-cycle invariant is the key correctness property: two concurrent
`room.disconnect()` calls poison the livekit-rtc FFI's asyncio loop and
take down the relay until systemd restarts it.
"""

from __future__ import annotations

import io
from typing import Any

import pytest

from baleobala.vpn.session_reaper import SessionReaper


# ---- Fakes -------------------------------------------------------------


class _FakeSession:
    """Minimal LiveKitSession-like object the reaper introspects."""

    def __init__(
        self,
        *,
        terminal: bool = False,
        state: Any = None,
        raise_on_terminal_check: bool = False,
    ) -> None:
        self._terminal = terminal
        self._state = state
        self._stop_calls = 0
        self._raise_on_terminal_check = raise_on_terminal_check

    def is_terminal(self) -> bool:
        if self._raise_on_terminal_check:
            raise RuntimeError("session probe blew up")
        return self._terminal

    def stop(self) -> None:
        self._stop_calls += 1


class _FakeMesh:
    def __init__(self) -> None:
        self.dropped: list[int] = []

    def drop_client(self, peer_id: int) -> None:
        self.dropped.append(peer_id)


class _FakeControl:
    def __init__(self) -> None:
        self.released: list[int] = []

    def release_mesh_assignment(self, peer_id: int) -> None:
        self.released.append(peer_id)


class _FakeAllocator:
    def __init__(self) -> None:
        self.left: list[int] = []

    def leave(self, peer_id: int) -> None:
        self.left.append(peer_id)


@pytest.fixture
def fakes():
    sessions: list[tuple[int, Any, Any]] = []
    mesh = _FakeMesh()
    control = _FakeControl()
    allocator = _FakeAllocator()
    log = io.StringIO()
    reaper = SessionReaper(
        sessions=sessions,
        mesh=mesh,
        control=control,
        allocator=allocator,
        cycle_seconds=0.0,
        log_stream=log,
    )
    return reaper, sessions, mesh, control, allocator, log


# ---- Tests -------------------------------------------------------------


def test_tick_returns_false_when_no_sessions(fakes):
    reaper, sessions, mesh, *_ = fakes
    assert reaper.tick() is False
    assert sessions == []
    assert mesh.dropped == []


def test_tick_returns_false_when_all_sessions_running(fakes):
    reaper, sessions, mesh, *_ = fakes
    sessions.append((42, _FakeSession(terminal=False), object()))
    sessions.append((43, _FakeSession(terminal=False), object()))
    assert reaper.tick() is False
    assert len(sessions) == 2
    assert mesh.dropped == []


def test_reaper_processes_at_most_one_per_cycle(fakes):
    """Regression for 9fea7eb. With three terminal sessions present, a
    single tick must reap exactly one; the other two wait for the next
    tick. This is the property that keeps the livekit-rtc FFI singleton
    from being asked to tear down two rooms at the same instant."""
    reaper, sessions, mesh, control, allocator, _ = fakes
    s1 = _FakeSession(terminal=True)
    s2 = _FakeSession(terminal=True)
    s3 = _FakeSession(terminal=True)
    sessions.append((101, s1, object()))
    sessions.append((102, s2, object()))
    sessions.append((103, s3, object()))

    assert reaper.tick() is True
    assert len(sessions) == 2, "reaper took more than one session in a single cycle"
    # Exactly one stop() call across the three sessions.
    assert s1._stop_calls + s2._stop_calls + s3._stop_calls == 1
    # And exactly one downstream cleanup chain ran.
    assert len(mesh.dropped) == 1
    assert len(control.released) == 1
    assert len(allocator.left) == 1

    # Second tick takes the next one.
    assert reaper.tick() is True
    assert len(sessions) == 1
    assert len(mesh.dropped) == 2

    # Third tick takes the last one; fourth finds nothing.
    assert reaper.tick() is True
    assert reaper.tick() is False
    assert sessions == []


def test_reaper_skips_stop_for_failed_sessions(fakes):
    """Regression for 9fea7eb (FAILED skip).

    A FAILED session has already closed its own asyncio loop. Calling
    stop() again would just join a dead thread, but the act of running
    `room.disconnect()` from a *concurrent* reap is what poisons the
    FFI singleton. The reaper must skip stop() for FAILED sessions and
    still complete the cleanup chain (drop_client, release, leave)."""
    from baleobala.bale.livekit_backend import LiveKitSessionState

    reaper, sessions, mesh, control, allocator, _ = fakes
    failed = _FakeSession(terminal=True, state=LiveKitSessionState.FAILED)
    sessions.append((201, failed, object()))

    assert reaper.tick() is True
    assert failed._stop_calls == 0, (
        "reaper called stop() on a FAILED session; that re-introduces the "
        "FFI poison race fixed in 9fea7eb"
    )
    assert mesh.dropped == [201]
    assert control.released == [201]
    assert allocator.left == [201]


def test_reaper_calls_stop_for_running_terminal_sessions(fakes):
    """Inverse of the FAILED-skip test: a session that is terminal but
    not in FAILED state (i.e. RUNNING-or-STOPPING when reaped) must
    have stop() invoked exactly once before the cleanup chain."""
    reaper, sessions, *_ = fakes
    running_terminal = _FakeSession(terminal=True, state=None)
    sessions.append((301, running_terminal, object()))

    reaper.tick()
    assert running_terminal._stop_calls == 1


def test_reaper_treats_probe_exception_as_terminal_and_skips_stop(fakes):
    """If `sess.is_terminal()` raises, the reaper falls back to "terminal
    AND failed" — it still removes the session from the list, but does
    not call stop() (the session is in an unknown state, calling stop()
    might re-trigger the original exception or worse)."""
    reaper, sessions, mesh, *_ = fakes
    blowing_up = _FakeSession(raise_on_terminal_check=True)
    sessions.append((401, blowing_up, object()))

    assert reaper.tick() is True
    assert blowing_up._stop_calls == 0
    assert sessions == []
    assert mesh.dropped == [401]


def test_reaper_emits_audit_line_with_failed_flag(fakes):
    """The stderr line is part of the operator triage workflow; preserve
    its format including `(failed=True/False)`."""
    from baleobala.bale.livekit_backend import LiveKitSessionState

    reaper, sessions, _, _, _, log = fakes
    failed = _FakeSession(terminal=True, state=LiveKitSessionState.FAILED)
    sessions.append((501, failed, object()))
    reaper.tick()
    out = log.getvalue()
    assert "[vpn-mesh] reaper: cleaning up dead session peer=501 (failed=True)" in out


def test_reaper_continues_when_downstream_cleanup_raises(fakes):
    """drop_client / release_mesh_assignment / allocator.leave failures
    must not stop the reaper from removing the session from the list —
    otherwise a single failing peer would block all future reaps."""
    reaper, sessions, _mesh, _control, _alloc, _ = fakes

    class BlowingMesh:
        def drop_client(self, peer_id: int):
            raise RuntimeError("mesh blew up")

    class BlowingControl:
        def release_mesh_assignment(self, peer_id: int):
            raise RuntimeError("control blew up")

    class BlowingAllocator:
        def leave(self, peer_id: int):
            raise RuntimeError("allocator blew up")

    sessions.append((601, _FakeSession(terminal=True), object()))
    reaper._mesh = BlowingMesh()
    reaper._control = BlowingControl()
    reaper._allocator = BlowingAllocator()

    # Must not raise; session still removed.
    assert reaper.tick() is True
    assert sessions == []


def test_start_stop_lifecycle_is_idempotent(fakes):
    reaper, *_ = fakes
    reaper.start()
    reaper.start()  # second start is a no-op
    reaper.stop()
    reaper.stop()  # idempotent shutdown
