"""Tests for the VPN supervisor: reconnect loop + checkpoint persistence."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from userbot_bale.control.observability import StructuredEventRecorder
from userbot_bale.vpn.supervisor import (
    SessionCheckpoint,
    SupervisedRunner,
    SupervisorConfig,
    load_checkpoint,
)


class _FakeTun:
    def __init__(self) -> None:
        self.name = "tun-test"
        self._closed = False

    def read_packet(self, bufsize: int = 2048):  # noqa: ARG002
        while not self._closed:
            time.sleep(0.05)
        return None

    def write_packet(self, pkt: bytes) -> int:  # noqa: ARG002
        return 0

    def close(self) -> None:
        self._closed = True


class _FakeTransport:
    """Transport that 'dies' after a short time, so the supervisor reconnects."""

    MTU_BYTES = 1024

    def __init__(self, lifetime: float = 0.05) -> None:
        self._lifetime = lifetime
        self._started = time.time()
        self._on_frame = None
        self._closed = threading.Event()

    def start(self, on_frame) -> None:  # pragma: no cover — not exercised
        self._on_frame = on_frame

    def send(self, frame: bytes) -> None:  # pragma: no cover
        pass

    def close(self) -> None:
        self._closed.set()

    @property
    def closed(self) -> bool:
        return self._closed.is_set() or (time.time() - self._started) > self._lifetime


def test_checkpoint_roundtrip(tmp_path: Path) -> None:
    cp = SessionCheckpoint(peer="bob", sess_id=42, backend="linux-tun", tun_name="vpn0")
    data = cp.to_dict()
    restored = SessionCheckpoint.from_dict(data)
    assert restored.peer == "bob"
    assert restored.sess_id == 42
    assert restored.backend == "linux-tun"


def test_load_checkpoint_missing(tmp_path: Path) -> None:
    assert load_checkpoint(tmp_path / "absent.json") is None


def test_supervisor_retries_transport_factory_failures(tmp_path: Path) -> None:
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        raise RuntimeError("boom")

    sup = SupervisedRunner(
        _FakeTun(),
        factory,
        supervisor_config=SupervisorConfig(
            initial_backoff=0.02, max_backoff=0.02, backoff_factor=1.0, max_retries=None
        ),
        checkpoint_path=tmp_path / "ck.json",
    )
    sup.start()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and calls["n"] < 2:
        time.sleep(0.01)
    sup.stop()
    assert calls["n"] >= 2

    ck = load_checkpoint(tmp_path / "ck.json")
    # stop() unlinks the checkpoint on clean shutdown
    assert ck is None


def test_supervisor_persists_checkpoint_while_running(tmp_path: Path) -> None:
    path = tmp_path / "ck.json"
    recorder = StructuredEventRecorder(component="test-supervisor")

    factory_event = threading.Event()

    def factory():
        factory_event.set()
        raise RuntimeError("blocked")

    sup = SupervisedRunner(
        _FakeTun(),
        factory,
        supervisor_config=SupervisorConfig(
            initial_backoff=1.0, max_backoff=1.0, max_retries=None
        ),
        checkpoint_path=path,
        recorder=recorder,
    )
    sup.start()
    assert factory_event.wait(2.0)
    deadline = time.monotonic() + 2.0
    ck = None
    while time.monotonic() < deadline:
        ck = load_checkpoint(path)
        if ck is not None and any(e["event"] == "supervisor_retry_scheduled" for e in recorder.events):
            break
        time.sleep(0.02)
    assert ck is not None
    assert ck.last_error == "blocked"
    assert any(event["event"] == "transport_setup_failed" for event in recorder.events)
    assert any(event["event"] == "supervisor_retry_scheduled" for event in recorder.events)
    sup.stop()
