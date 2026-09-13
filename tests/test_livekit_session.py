from __future__ import annotations

import asyncio
import queue
import threading
import time
from concurrent.futures import Future
from types import SimpleNamespace

import pytest

from userbot_bale.bale import livekit_backend as lk


class _FakeTrackKind:
    KIND_AUDIO = "audio"
    KIND_VIDEO = "video"


class _FakeTrackSource:
    SOURCE_MICROPHONE = "microphone"
    SOURCE_CAMERA = "camera"


class _FakeTrackPublishOptions:
    def __init__(self, *, source: str) -> None:
        self.source = source


class _FakeAudioFrame:
    def __init__(self, data: bytes, sample_rate: int, num_channels: int, samples_per_channel: int) -> None:
        self.data = data
        self.sample_rate = sample_rate
        self.num_channels = num_channels
        self.samples_per_channel = samples_per_channel


class _FakeVideoFrame:
    def __init__(self, width: int, height: int, buffer_type, data: bytes) -> None:  # noqa: ANN001
        self.width = width
        self.height = height
        self.buffer_type = buffer_type
        self.data = data


class _FakeAudioSource:
    def __init__(self, sample_rate: int, channels: int) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.frames: list[_FakeAudioFrame] = []

    async def capture_frame(self, frame: _FakeAudioFrame) -> None:
        self.frames.append(frame)


class _FakeVideoSource:
    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.frames: list[_FakeVideoFrame] = []

    async def capture_frame(self, frame: _FakeVideoFrame) -> None:
        self.frames.append(frame)


class _FakeLocalParticipant:
    def __init__(self) -> None:
        self.published_tracks: list[tuple[object, object]] = []
        self.published_data: list[tuple[bytes, bool, str]] = []

    async def publish_track(self, track, options) -> None:  # noqa: ANN001
        self.published_tracks.append((track, options))

    async def publish_data(self, payload: bytes, *, reliable: bool, topic: str) -> None:
        self.published_data.append((payload, reliable, topic))


class _FakeRoom:
    connect_error: BaseException | None = None
    connect_wait: threading.Event | None = None
    instances: list["_FakeRoom"] = []

    def __init__(self) -> None:
        self.name = "fake-room"
        self.local_participant = _FakeLocalParticipant()
        self.handlers: dict[str, list] = {}
        self.disconnected = False
        type(self).instances.append(self)

    def on(self, event_name: str):
        def decorator(fn):
            self.handlers.setdefault(event_name, []).append(fn)
            return fn

        return decorator

    async def connect(self, url: str, token: str) -> None:
        assert url
        assert token
        if type(self).connect_wait is not None:
            type(self).connect_wait.wait(timeout=2.0)
        if type(self).connect_error is not None:
            raise type(self).connect_error

    async def disconnect(self) -> None:
        self.disconnected = True


class _FakeAudioStream:
    def __init__(self, track, sample_rate: int, num_channels: int) -> None:  # noqa: ANN001
        self._events: "queue.Queue[object | None]" = queue.Queue()
        self.sample_rate = sample_rate
        self.num_channels = num_channels
        track._audio_stream = self

    def push_pcm(self, data: bytes, *, sample_rate: int, num_channels: int) -> None:
        frame = SimpleNamespace(data=data, sample_rate=sample_rate, num_channels=num_channels)
        self._events.put(SimpleNamespace(frame=frame))

    def close(self) -> None:
        self._events.put(None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        while True:
            try:
                item = self._events.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.01)
                continue
            if item is None:
                raise StopAsyncIteration
            return item


class _FakeVideoStream(_FakeAudioStream):
    pass


class _FakeLocalAudioTrack:
    @staticmethod
    def create_audio_track(name: str, source: _FakeAudioSource):  # noqa: ARG004
        return SimpleNamespace(source=source)


class _FakeLocalVideoTrack:
    @staticmethod
    def create_video_track(name: str, source: _FakeVideoSource):  # noqa: ARG004
        return SimpleNamespace(source=source)


def _install_fake_rtc(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeRoom.connect_error = None
    _FakeRoom.connect_wait = None
    _FakeRoom.instances = []
    fake_rtc = SimpleNamespace(
        Room=_FakeRoom,
        AudioSource=_FakeAudioSource,
        VideoSource=_FakeVideoSource,
        AudioFrame=_FakeAudioFrame,
        VideoFrame=_FakeVideoFrame,
        AudioStream=_FakeAudioStream,
        VideoStream=_FakeVideoStream,
        LocalAudioTrack=_FakeLocalAudioTrack,
        LocalVideoTrack=_FakeLocalVideoTrack,
        TrackPublishOptions=_FakeTrackPublishOptions,
        TrackSource=_FakeTrackSource,
        TrackKind=_FakeTrackKind,
        VideoBufferType=SimpleNamespace(RGB24="rgb24"),
    )
    monkeypatch.setattr(lk, "rtc", fake_rtc)
    monkeypatch.setattr(lk, "_HAS_LIVEKIT", True)


def test_livekit_session_start_stop_and_repeated_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_rtc(monkeypatch)

    session = lk.LiveKitSession(url="ws://fake", token="token", identity="alice")
    session.start()

    assert session.state == "running"
    assert session.is_running() is True
    assert _FakeRoom.instances[-1].disconnected is False

    session.stop()
    assert session.state == "stopped"
    assert session.is_terminal() is True
    assert _FakeRoom.instances[-1].disconnected is True

    session.stop()
    assert session.state == "stopped"


def test_livekit_session_failed_start_cleans_up(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_rtc(monkeypatch)
    _FakeRoom.connect_error = RuntimeError("connect boom")

    session = lk.LiveKitSession(url="ws://fake", token="token", identity="alice")

    with pytest.raises(RuntimeError, match="connect boom"):
        session.start()

    assert session.state == "failed"
    assert isinstance(session.terminal_error, RuntimeError)
    session.stop()
    assert session.is_terminal() is True


def test_livekit_session_rejects_second_start(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_rtc(monkeypatch)

    session = lk.LiveKitSession(url="ws://fake", token="token", identity="alice")
    session.start()

    with pytest.raises(RuntimeError, match="already started"):
        session.start()

    session.stop()
    with pytest.raises(RuntimeError, match="single-use"):
        session.start()


def test_submit_coro_accepts_future_like_awaitable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_rtc(monkeypatch)

    session = lk.LiveKitSession(url="ws://fake", token="token", identity="alice")
    session.start()
    try:
        fut: Future[None] = Future()
        fut.set_result(None)
        submitted = session._submit_coro(fut, require_running=False)
        assert submitted.result() is None
    finally:
        session.stop()


def test_livekit_source_unblocks_on_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_rtc(monkeypatch)

    session = lk.LiveKitSession(url="ws://fake", token="token", identity="alice")
    session.start()
    source = session.source()
    seen: list[object] = []

    def _read() -> None:
        for block in source.iter_blocks():
            seen.append(block)
        seen.append("closed")

    reader = threading.Thread(target=_read, daemon=True)
    reader.start()
    session.stop()
    reader.join(timeout=2.0)

    assert not reader.is_alive()
    assert seen == ["closed"]


def test_livekit_data_channel_send_after_stop_fails_and_recv_unblocks(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_rtc(monkeypatch)

    session = lk.LiveKitSession(url="ws://fake", token="token", identity="alice")
    session.start()
    channel = session.data_channel(topic="vpn")
    received: list[bytes | None] = []

    def _recv() -> None:
        received.append(channel.recv_bytes(timeout=5.0))

    reader = threading.Thread(target=_recv, daemon=True)
    reader.start()
    session.stop()
    reader.join(timeout=2.0)

    assert not reader.is_alive()
    assert received == [None]
    with pytest.raises(RuntimeError, match="state=stopped"):
        channel.send_bytes(b"late")


def test_participant_disconnect_promotes_to_terminal_after_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for f80c59b (grace-then-terminal).

    A `participant_disconnected` event must NOT immediately terminate the
    session — earlier code did that, killing live VPN tunnels on a
    transient SFU blip. The watchdog in `_run` waits PEER_LOST_GRACE_SECS
    and only then promotes the disconnect to a terminal failure so the
    relay's reaper can free the slot."""
    _install_fake_rtc(monkeypatch)
    monkeypatch.setattr(lk, "PEER_LOST_GRACE_SECS", 0.1)

    session = lk.LiveKitSession(url="ws://fake", token="token", identity="alice")
    session.start()
    try:
        room = _FakeRoom.instances[-1]
        # Fire the participant_disconnected handler the same way the SFU would.
        participant = SimpleNamespace(identity="bob")
        room.handlers["participant_disconnected"][0](participant)

        # Immediately after the event, session is still running — the grace
        # window has not elapsed yet.
        assert session.is_terminal() is False, (
            "session terminated immediately on participant_disconnected; "
            "grace window from f80c59b is gone"
        )

        # Wait past the grace window for the watchdog (which polls every
        # 0.25s in real code) to promote to terminal.
        deadline = time.time() + 2.0
        while not session.is_terminal() and time.time() < deadline:
            time.sleep(0.05)

        assert session.is_terminal() is True, (
            "session did not promote to terminal after grace window expired"
        )
        assert isinstance(session.terminal_error, RuntimeError)
        assert "remote participant lost" in str(session.terminal_error)
    finally:
        session.stop()


def test_participant_reconnect_within_grace_clears_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `participant_connected` arriving inside the grace window must clear
    `_peer_disconnect_at` so the watchdog does not promote to terminal —
    the blip-tolerant half of f80c59b."""
    _install_fake_rtc(monkeypatch)
    monkeypatch.setattr(lk, "PEER_LOST_GRACE_SECS", 0.5)

    session = lk.LiveKitSession(url="ws://fake", token="token", identity="alice")
    session.start()
    try:
        room = _FakeRoom.instances[-1]
        participant = SimpleNamespace(identity="bob")
        room.handlers["participant_disconnected"][0](participant)
        assert session._peer_disconnect_at is not None

        # Reconnect arrives well inside the grace window.
        time.sleep(0.05)
        room.handlers["participant_connected"][0](participant)

        # The disconnect timestamp is cleared and the session stays alive
        # past the grace window.
        assert session._peer_disconnect_at is None
        time.sleep(0.6)  # well past PEER_LOST_GRACE_SECS=0.5
        assert session.is_terminal() is False, (
            "session marked terminal despite participant returning within grace"
        )
    finally:
        session.stop()


def test_livekit_audio_subscription_task_drains_on_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_rtc(monkeypatch)

    session = lk.LiveKitSession(url="ws://fake", token="token", identity="alice")
    session.start()
    room = _FakeRoom.instances[-1]
    track = SimpleNamespace(kind=_FakeTrackKind.KIND_AUDIO)
    participant = SimpleNamespace(identity="bob")
    room.handlers["track_subscribed"][0](track, None, participant)
    stream = track._audio_stream
    stream.push_pcm(b"\x00\x00" * 1200, sample_rate=lk.SAMPLE_RATE, num_channels=1)
    deadline = time.time() + 1.0
    while session._incoming.qsize() == 0 and time.time() < deadline:
        time.sleep(0.01)
    iterator = session.source().iter_blocks()
    first = next(iterator)
    assert len(first) == 1024

    session.stop()
    assert session.state == "stopped"
