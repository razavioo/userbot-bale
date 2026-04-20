"""
Video-QR transport: VPN frames ride as QR codes through a LiveKit
video track inside the Bale call.

Pipeline (sender):
    send_bytes(b) → enqueue
    worker thread: pop frame → QR encode (PIL) → numpy RGB24 →
                   session.publish_video().push_frame(array)
    ticks at `fps` Hz (default 8). A hold-last-frame policy means
    if the send queue is empty we just re-push the idle frame, so
    the peer's decoder doesn't starve.

Pipeline (receiver):
    session.on_video_frame(cb) fires with BGR ndarrays →
    cv2.QRCodeDetector().detectAndDecode → bytes →
    dedup against recent payload hashes → recv queue.

Why OpenCV's detector rather than pyzbar? No system libzbar required,
one pure-pip dep (`opencv-python-headless`), and the built-in detector
handles WebRTC's re-compressed video acceptably.

Throughput estimate: QR version 10 Alphanumeric at ECC L holds 395 chars,
binary holds ~271 bytes. At 8 fps with successful decode rate ~50%
through Bale's video pipeline, expect ~1 KB/s — two orders of
magnitude above audio, two below DataChannel.

Deps installed in the [vpn] extra: qrcode, pillow, opencv-python-headless.
"""

from __future__ import annotations

import hashlib
import logging
import queue
import threading
import time
from typing import Optional

log = logging.getLogger(__name__)

WIDTH = 480
HEIGHT = 480
DEFAULT_FPS = 8


class VideoQrTransportNotReady(RuntimeError):
    """Raised if optional deps (opencv, qrcode, pillow) aren't installed."""


def _require_deps():  # type: ignore[no-untyped-def]
    try:
        import cv2  # noqa: F401
        import numpy as np  # noqa: F401
        import qrcode  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError as e:
        raise VideoQrTransportNotReady(
            "video-qr transport needs opencv-python-headless, qrcode, "
            "pillow. Install with: pip install 'baleobala[vpn-video]'"
        ) from e


class VideoQrTransport:
    # Conservative binary-capacity cap — QR version 10 ECC L holds ~271
    # bytes binary; we leave headroom for our own framing.
    MTU = 240
    RATE_HINT = 1000.0  # bytes/s after video re-encoding

    def __init__(
        self,
        session,  # type: ignore[no-untyped-def]  # LiveKitSession
        *,
        width: int = WIDTH,
        height: int = HEIGHT,
        fps: int = DEFAULT_FPS,
    ) -> None:
        _require_deps()
        import numpy as np

        self.mtu = self.MTU
        self.rate_hint = self.RATE_HINT
        self._session = session
        self._width = width
        self._height = height
        self._fps = fps

        # Publish our video track on the session.
        self._video_out = session.publish_video(width, height)
        # Idle frame = solid gray; pushed when the send queue is empty.
        self._idle_frame = np.full((height, width, 3), 128, dtype=np.uint8)
        # Last encoded frame we pushed — for repeat-on-idle.
        self._last_frame = self._idle_frame

        self._send_q: "queue.Queue[bytes]" = queue.Queue()
        self._recv_q: "queue.Queue[bytes]" = queue.Queue()
        self._seen: set[bytes] = set()  # payload hashes
        self._seen_order: list[bytes] = []

        self._closed = False
        self._stop = threading.Event()
        self._tx_thread = threading.Thread(
            target=self._run_tx, name="vpn-qr-tx", daemon=True
        )
        self._tx_thread.start()

        # Subscribe to inbound video. The callback runs on the
        # LiveKit asyncio thread; we do CPU-bound decode there (QR
        # detection on a 480x480 frame is ~5ms, fine).
        session.on_video_frame(self._on_frame)

    # ---- Transport protocol ---------------------------------------------

    def send_bytes(self, data: bytes) -> None:
        if self._closed:
            return
        if len(data) > self.MTU:
            raise ValueError(f"frame {len(data)} > qr MTU {self.MTU}")
        self._send_q.put(data)

    def recv_bytes(self, timeout: Optional[float] = None) -> Optional[bytes]:
        if self._closed:
            return None
        try:
            return self._recv_q.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        if self._tx_thread.is_alive():
            self._tx_thread.join(timeout=2)

    # ---- internals -------------------------------------------------------

    def _run_tx(self) -> None:
        period = 1.0 / max(1, self._fps)
        while not self._stop.is_set():
            try:
                # Short wait so we keep pushing at ~fps even when idle;
                # video codecs handle unchanged frames efficiently, and
                # steady frame delivery is what keeps the track alive.
                payload = self._send_q.get(timeout=period)
                frame = self._encode_qr(payload)
                self._last_frame = frame
            except queue.Empty:
                frame = self._last_frame
            try:
                self._video_out.push_frame(frame)
            except Exception:  # noqa: BLE001
                log.exception("push_frame failed")

    def _encode_qr(self, payload: bytes):  # type: ignore[no-untyped-def]
        import numpy as np
        import qrcode
        from PIL import Image

        qr = qrcode.QRCode(
            version=None,  # auto-fit
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=8,
            border=4,
        )
        # qrcode's API takes str or bytes; add_data(bytes) encodes in
        # 'binary' mode which is what we want.
        qr.add_data(payload)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        # Resize to exact publish dims so captured_frame matches source.
        img = img.resize((self._width, self._height), Image.NEAREST)
        return np.array(img, dtype=np.uint8)

    def _on_frame(self, bgr) -> None:  # type: ignore[no-untyped-def]
        import cv2

        if self._closed:
            return
        try:
            det = getattr(self, "_detector", None)
            if det is None:
                det = self._detector = cv2.QRCodeDetector()
            # detectAndDecodeBytes (OpenCV ≥4.7) returns raw bytes —
            # essential for arbitrary binary payloads, since the str-
            # returning detectAndDecode truncates at embedded NULs.
            data, _, _ = det.detectAndDecodeBytes(bgr)
            if data is None or len(data) == 0:
                return
            payload = bytes(data)
        except Exception:  # noqa: BLE001
            log.exception("QR decode failed")
            return
        h = hashlib.blake2b(payload, digest_size=16).digest()
        if h in self._seen:
            return
        self._seen.add(h)
        self._seen_order.append(h)
        while len(self._seen_order) > 512:
            old = self._seen_order.pop(0)
            self._seen.discard(old)
        self._recv_q.put(payload)
