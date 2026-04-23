from __future__ import annotations

import threading as real_threading

import pytest

from baleobala.vpn import keepalive


class _RecordingSession:
    def __init__(self) -> None:
        self.calls: list[tuple[bytes, str, bool]] = []
        self.published = real_threading.Event()

    def _submit_data(self, payload: bytes, *, topic: str, reliable: bool) -> None:
        self.calls.append((payload, topic, reliable))
        self.published.set()


class _ControlledEvent:
    def __init__(self, *, exit_after_first_publish: bool) -> None:
        self._set = real_threading.Event()
        self._wait_count = 0
        self._exit_after_first_publish = exit_after_first_publish
        self.wait_calls: list[float | None] = []

    def wait(self, timeout: float | None = None) -> bool:
        self.wait_calls.append(timeout)
        self._wait_count += 1
        if self._set.is_set():
            return True
        if self._wait_count == 1:
            return False
        if self._exit_after_first_publish:
            return True
        self._set.wait(0.01)
        return self._set.is_set()

    def set(self) -> None:
        self._set.set()


def test_livekit_keepalive_publishes_on_expected_topic_and_interval() -> None:
    event = _ControlledEvent(exit_after_first_publish=True)
    session = _RecordingSession()
    ka = keepalive.LiveKitKeepalive(session, interval=0.125)
    ka._stop = event  # type: ignore[assignment]
    ka.start()

    assert session.published.wait(timeout=1.0)
    ka.stop()

    assert session.calls == [(keepalive.KEEPALIVE_PAYLOAD, keepalive.KEEPALIVE_TOPIC, False)]
    assert event.wait_calls[0] == pytest.approx(0.125)


def test_livekit_keepalive_stop_terminates_thread_cleanly() -> None:
    event = _ControlledEvent(exit_after_first_publish=False)
    session = _RecordingSession()
    ka = keepalive.LiveKitKeepalive(session, interval=0.05)
    ka._stop = event  # type: ignore[assignment]
    ka.start()

    assert session.published.wait(timeout=1.0)
    thread = ka._thread
    assert thread is not None

    ka.stop()

    assert thread.is_alive() is False
    assert ka._thread is None
    assert event.wait_calls[0] == pytest.approx(0.05)
