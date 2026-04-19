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
        fut = asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
        try:
            fut.result(timeout=5)
        except Exception:  # noqa: BLE001
            log.exception("LiveKit shutdown failed")
        self._stopped.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        self._incoming.put(None)

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
