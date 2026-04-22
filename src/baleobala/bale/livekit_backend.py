"""
LiveKit-backed AudioSink / AudioSource.

Bale voice calls are standard WebRTC hosted on LiveKit's SFU
(see `lib/arm64-v8a/liblkjingle_peerconnection_so.so` in the APK and
the `ai.bale.proto.MeetOuterClass.RequestStartLiveKitCall` RPC that
returns the room URL + access token).

LiveKit is standards-compliant WebRTC, so we can use the official
`livekit-rtc` Python SDK without reverse-engineering the media layer.
We just need a URL and a token — obtained either via:
  * the Bale app (sniffed with mitmproxy on a real call), or
  * the Bale API client in baleobala.bale.api (when the transport
    layer is complete; see api.py).

PCM format
----------
- baleobala's codec emits float32 mono at 48 kHz.
- LiveKit's AudioSource accepts int16 PCM at any sample rate; we
  specify 48 kHz and pass 10 ms frames (480 samples), which is what
  Opus (LiveKit's default audio codec) consumes natively.
- Incoming audio is int16 at the track's native rate. We resample on
  the fly to 48 kHz float32 for the GGWave decoder if it differs.

Threading
---------
LiveKit's SDK is asyncio-based. To fit the synchronous
AudioSink/AudioSource protocols, we run the asyncio loop in a
dedicated thread and bridge via thread-safe queues. The consumer
(Transmitter / Receiver) sees blocking play()/iter_blocks() calls.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import queue
import threading
from typing import Iterator

import numpy as np

from baleobala.codec import SAMPLE_RATE

log = logging.getLogger(__name__)

LIVEKIT_SAMPLE_RATE = 48_000
LIVEKIT_FRAME_MS = 10
LIVEKIT_FRAME_SAMPLES = LIVEKIT_SAMPLE_RATE * LIVEKIT_FRAME_MS // 1000  # 480

try:  # soft dep; only Phase 3 callers need this
    from livekit import rtc  # type: ignore
    _HAS_LIVEKIT = True
except Exception:  # pragma: no cover - optional extra
    rtc = None  # type: ignore[assignment]
    _HAS_LIVEKIT = False


def _require_livekit() -> None:
    if not _HAS_LIVEKIT:
        raise ImportError(
            "livekit-rtc is not installed. Install with: pip install 'baleobala[bale]'"
        )


def _float_to_int16(block: np.ndarray) -> np.ndarray:
    return np.clip(block * 32768.0, -32768, 32767).astype(np.int16)


def _int16_to_float(pcm: np.ndarray) -> np.ndarray:
    return (pcm.astype(np.float32) / 32768.0).astype(np.float32)


class _IgnoreClosedLoopFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return "error putting to queue: Event loop is closed" not in message


class LiveKitSession:
    """
    Manages a LiveKit room connection on a dedicated asyncio thread.

    Usage:
        session = LiveKitSession(url=..., token=...)
        session.start()
        try:
            sink = session.sink()
            source = session.source()
            # hand sink to Transmitter, source to Receiver
        finally:
            session.stop()

    Only one local audio track is published; all remote audio is mixed
    into the single source stream (we concatenate by time, not peer,
    which is what GGWave's streaming decoder expects).
    """

    def __init__(self, url: str, token: str, identity: str = "baleobala") -> None:
        _require_livekit()
        self.url = url
        self.token = token
        self.identity = identity
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._room: "rtc.Room | None" = None  # type: ignore[name-defined]
        self._audio_source: "rtc.AudioSource | None" = None  # type: ignore[name-defined]
        # Unbounded: the async producer pushes one 10 ms frame at a time
        # (100 Hz) and the sync consumer (GGWave decoder) is fast enough to
        # keep up. Bounding here caused lost frames on startup before the
        # decoder drained the initial settling period; memory is a safer
        # bet than dropped audio.
        self._incoming: "queue.Queue[np.ndarray | None]" = queue.Queue()
        # Inbound LiveKit DataChannel payloads, filtered by topic. One
        # queue per topic so the VPN transport and any future control
        # channel don't interleave.
        self._data_queues: "dict[str, queue.Queue[bytes | None]]" = {}
        self._data_lock = threading.Lock()
        # Video out: a published track backed by a VideoSource we push frames to.
        self._video_source: "rtc.VideoSource | None" = None  # type: ignore[name-defined]
        self._video_width = 0
        self._video_height = 0
        # Video in: single callback invoked from asyncio with ndarray frames.
        self._video_frame_cb = None  # type: ignore[var-annotated]

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("session already started")
        self._thread = threading.Thread(
            target=self._thread_main, name="baleobala-livekit", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout=15):
            raise RuntimeError("LiveKit room did not become ready within 15s")

    def stop(self) -> None:
        if self._loop is None:
            return
        livekit_logger = logging.getLogger("livekit")
        suppress_filter = _IgnoreClosedLoopFilter()
        livekit_logger.addFilter(suppress_filter)
        fut = asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
        try:
            try:
                fut.result(timeout=5)
            except Exception:  # noqa: BLE001
                log.exception("LiveKit shutdown failed")
            self._stopped.set()
            if self._thread is not None:
                self._thread.join(timeout=5)
                self._thread = None
            self._incoming.put(None)
        finally:
            with contextlib.suppress(Exception):
                livekit_logger.removeFilter(suppress_filter)

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run())
        finally:
            loop.close()
            self._loop = None

    async def _run(self) -> None:
        room = rtc.Room()
        self._room = room

        @room.on("track_subscribed")  # type: ignore[misc]
        def on_track_subscribed(track, publication, participant):  # type: ignore[no-untyped-def]
            if track.kind != rtc.TrackKind.KIND_AUDIO:
                return
            log.debug("subscribed to audio track from %s", participant.identity)
            stream = rtc.AudioStream(
                track, sample_rate=LIVEKIT_SAMPLE_RATE, num_channels=1,
            )
            asyncio.ensure_future(self._consume_audio(stream))

        @room.on("track_subscribed")  # type: ignore[misc]
        def on_video_subscribed(track, publication, participant):  # type: ignore[no-untyped-def]
            if track.kind != rtc.TrackKind.KIND_VIDEO:
                return
            if self._video_frame_cb is None:
                return
            log.debug("subscribed to video track from %s", participant.identity)
            stream = rtc.VideoStream(track)
            asyncio.ensure_future(self._consume_video(stream))

        @room.on("data_received")  # type: ignore[misc]
        def on_data_received(packet):  # type: ignore[no-untyped-def]
            topic = packet.topic or ""
            with self._data_lock:
                q = self._data_queues.get(topic)
            if q is not None:
                q.put(bytes(packet.data))
            else:
                log.debug("data on unhandled topic=%r (%d bytes)", topic, len(packet.data))

        await room.connect(self.url, self.token)
        log.info("LiveKit room connected: %s identity=%s",
                 room.name, self.identity)

        source = rtc.AudioSource(LIVEKIT_SAMPLE_RATE, 1)
        self._audio_source = source
        track = rtc.LocalAudioTrack.create_audio_track("baleobala", source)
        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        await room.local_participant.publish_track(track, options)
        log.info("local audio track published")

        self._ready.set()

        while not self._stopped.is_set():
            await asyncio.sleep(0.25)

    async def _consume_audio(self, stream) -> None:  # type: ignore[no-untyped-def]
        async for event in stream:
            frame = event.frame
            pcm = np.frombuffer(frame.data, dtype=np.int16)
            if frame.num_channels > 1:
                pcm = pcm.reshape(-1, frame.num_channels).mean(axis=1).astype(np.int16)
            if frame.sample_rate != SAMPLE_RATE:
                pcm = _resample_linear(pcm, frame.sample_rate, SAMPLE_RATE)
            self._incoming.put(_int16_to_float(pcm))

    async def _shutdown(self) -> None:
        if self._room is not None:
            try:
                await self._room.disconnect()
                # Let SDK callbacks drain before the event loop goes away.
                await asyncio.sleep(0.25)
            except Exception:  # noqa: BLE001
                log.exception("room.disconnect failed")
            self._room = None

    # --- public factory methods for the AudioSink/AudioSource protocols

    def sink(self) -> "LiveKitSink":
        return LiveKitSink(self)

    def source(self) -> "LiveKitSource":
        return LiveKitSource(self)

    # --- internal hooks used by sink/source

    def _submit_outgoing(self, waveform: np.ndarray) -> None:
        if self._audio_source is None or self._loop is None:
            raise RuntimeError("session not started")
        samples = _float_to_int16(waveform)
        total = len(samples)
        pos = 0
        while pos < total:
            chunk = samples[pos : pos + LIVEKIT_FRAME_SAMPLES]
            if len(chunk) < LIVEKIT_FRAME_SAMPLES:
                pad = np.zeros(LIVEKIT_FRAME_SAMPLES - len(chunk), dtype=np.int16)
                chunk = np.concatenate([chunk, pad])
            frame = rtc.AudioFrame(
                chunk.tobytes(),
                LIVEKIT_SAMPLE_RATE,
                1,
                LIVEKIT_FRAME_SAMPLES,
            )
            fut = asyncio.run_coroutine_threadsafe(
                self._audio_source.capture_frame(frame), self._loop
            )
            try:
                fut.result(timeout=5)
            except Exception:  # noqa: BLE001
                log.exception("capture_frame failed")
                return
            pos += LIVEKIT_FRAME_SAMPLES

    def _iter_incoming(self) -> Iterator[np.ndarray]:
        while True:
            block = self._incoming.get()
            if block is None:
                return
            yield block

    # --- Video track (QR transport piggybacks here) ---

    def publish_video(self, width: int, height: int) -> "LiveKitVideoOut":
        """Create + publish a video track. Call only after start().
        Returns an object with push_frame(ndarray_rgb24) for the sender."""
        if self._room is None or self._loop is None:
            raise RuntimeError("session not started")
        if self._video_source is not None:
            raise RuntimeError("video already published")
        source = rtc.VideoSource(width, height)
        track = rtc.LocalVideoTrack.create_video_track("baleobala-qr", source)
        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA)
        fut = asyncio.run_coroutine_threadsafe(
            self._room.local_participant.publish_track(track, options),
            self._loop,
        )
        fut.result(timeout=10)
        self._video_source = source
        self._video_width = width
        self._video_height = height
        log.info("video track published (%dx%d)", width, height)
        return LiveKitVideoOut(self)

    def on_video_frame(self, callback) -> None:  # type: ignore[no-untyped-def]
        """Register a callback(frame_bgr: np.ndarray) invoked from an
        asyncio task whenever a remote peer publishes a video frame.
        Only one callback per session; subsequent calls replace it."""
        self._video_frame_cb = callback

    async def _consume_video(self, stream) -> None:  # type: ignore[no-untyped-def]
        async for event in stream:
            frame = event.frame
            if self._video_frame_cb is None:
                continue
            # Convert to BGR24 numpy for OpenCV consumption. frame is
            # typically I420; convert to RGB24 via the helper.
            try:
                rgb = frame.convert(rtc.VideoBufferType.RGB24)
                arr = np.frombuffer(rgb.data, dtype=np.uint8).reshape(
                    rgb.height, rgb.width, 3
                )
                # cv2 expects BGR; swap channels in-place.
                bgr = arr[:, :, ::-1].copy()
                self._video_frame_cb(bgr)
            except Exception:  # noqa: BLE001
                log.exception("video frame conversion failed")

    def _submit_video_frame(self, rgb24: np.ndarray) -> None:
        if self._video_source is None or self._loop is None:
            raise RuntimeError("video not published")
        if rgb24.shape != (self._video_height, self._video_width, 3):
            raise ValueError(
                f"frame shape {rgb24.shape} != published "
                f"({self._video_height},{self._video_width},3)"
            )
        frame = rtc.VideoFrame(
            self._video_width,
            self._video_height,
            rtc.VideoBufferType.RGB24,
            rgb24.tobytes(),
        )
        # Fire-and-forget: VideoSource.capture_frame is async but fast.
        # We schedule on the loop and don't wait — video throughput
        # would collapse if every frame blocked on round-trip.
        asyncio.run_coroutine_threadsafe(
            self._video_source.capture_frame(frame), self._loop
        )

    # --- DataChannel (WebRTC data channel inside the same LiveKit room) ---

    def data_channel(self, topic: str = "vpn", *, reliable: bool = True) -> "LiveKitDataChannel":
        """Register a DataChannel endpoint for `topic`. Call only after start()."""
        if self._room is None:
            raise RuntimeError("session not started")
        with self._data_lock:
            if topic not in self._data_queues:
                self._data_queues[topic] = queue.Queue()
        return LiveKitDataChannel(self, topic=topic, reliable=reliable)

    def _submit_data(self, payload: bytes, *, topic: str, reliable: bool) -> None:
        if self._room is None or self._loop is None:
            raise RuntimeError("session not started")
        fut = asyncio.run_coroutine_threadsafe(
            self._room.local_participant.publish_data(
                payload, reliable=reliable, topic=topic
            ),
            self._loop,
        )
        try:
            fut.result(timeout=5)
        except Exception:  # noqa: BLE001
            log.exception("publish_data failed (topic=%s, %d bytes)", topic, len(payload))

    def _recv_data(self, topic: str, timeout: float | None) -> bytes | None:
        with self._data_lock:
            q = self._data_queues.get(topic)
        if q is None:
            return None
        try:
            return q.get(timeout=timeout)
        except queue.Empty:
            return None

    def _close_data(self, topic: str) -> None:
        with self._data_lock:
            q = self._data_queues.pop(topic, None)
        if q is not None:
            q.put(None)


def _resample_linear(pcm: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Cheap linear resample. Adequate for data-over-voice; we don't need
    anti-aliasing quality because GGWave's decoder is tolerant and the
    upstream Opus stage already band-limits the signal."""
    if src_rate == dst_rate:
        return pcm
    n_src = len(pcm)
    n_dst = int(round(n_src * dst_rate / src_rate))
    if n_dst <= 0:
        return np.zeros(0, dtype=pcm.dtype)
    x_src = np.linspace(0.0, 1.0, num=n_src, endpoint=False)
    x_dst = np.linspace(0.0, 1.0, num=n_dst, endpoint=False)
    return np.interp(x_dst, x_src, pcm).astype(pcm.dtype)


class LiveKitSink:
    def __init__(self, session: LiveKitSession) -> None:
        self._session = session

    def play(self, waveform: np.ndarray) -> None:
        self._session._submit_outgoing(waveform)

    def close(self) -> None:  # session owns the lifecycle
        pass


class LiveKitSource:
    """
    Accumulates LiveKit's 10 ms (480-sample) frames into the larger
    1024-sample blocks GGWave's decoder requires. Feeding the native
    decoder smaller blocks silently fails to decode (empirically) —
    it seems to need a minimum window for its FFT/alignment stage.
    """

    DECODE_BLOCK = 1024

    def __init__(self, session: LiveKitSession) -> None:
        self._session = session
        self._closed = False

    def iter_blocks(self) -> Iterator[np.ndarray]:
        buf = np.zeros(0, dtype=np.float32)
        for frame in self._session._iter_incoming():
            if self._closed:
                return
            buf = np.concatenate([buf, frame])
            while len(buf) >= self.DECODE_BLOCK:
                yield buf[: self.DECODE_BLOCK]
                buf = buf[self.DECODE_BLOCK :]

    def close(self) -> None:
        self._closed = True


class LiveKitVideoOut:
    """Thin handle returned by LiveKitSession.publish_video(). The QR
    transport calls push_frame() at ~fps Hz."""

    def __init__(self, session: "LiveKitSession") -> None:
        self._session = session

    def push_frame(self, rgb24: np.ndarray) -> None:
        self._session._submit_video_frame(rgb24)


class LiveKitDataChannel:
    """
    Bidirectional byte channel over a LiveKit DataChannel, scoped by `topic`.

    With `reliable=True` (default) LiveKit delivers ordered, reliable
    bytes — so the VPN tunnel above can run with `window` set high and
    ARQ timeouts loose; the DataChannel itself is not going to reorder
    or drop. With `reliable=False` delivery is best-effort (lossy) and
    the tunnel's ARQ does real work.

    LiveKit's single-publish_data limit in the JS/SFU stack has
    historically been around 15 KiB. We expose a conservative 14 KiB
    MTU here; the tunnel core splits larger IP packets into frames that
    fit.
    """

    MTU = 14 * 1024
    RATE_HINT = 200_000.0  # bytes/s; realistic over Bale's SFU

    def __init__(
        self, session: "LiveKitSession", *, topic: str, reliable: bool
    ) -> None:
        self._session = session
        self._topic = topic
        self._reliable = reliable
        self._closed = False

    @property
    def mtu(self) -> int:
        return self.MTU

    @property
    def rate_hint(self) -> float:
        return self.RATE_HINT

    def send_bytes(self, data: bytes) -> None:
        if self._closed:
            return
        if len(data) > self.MTU:
            raise ValueError(f"frame {len(data)} > datachannel MTU {self.MTU}")
        self._session._submit_data(data, topic=self._topic, reliable=self._reliable)

    def recv_bytes(self, timeout: float | None = None) -> bytes | None:
        if self._closed:
            return None
        return self._session._recv_data(self._topic, timeout)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._session._close_data(self._topic)
