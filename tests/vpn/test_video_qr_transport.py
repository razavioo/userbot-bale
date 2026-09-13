"""
Unit test for the QR encode/decode roundtrip inside VideoQrTransport.

Bypasses the LiveKit plumbing by stubbing the session — we only want
to verify that bytes encoded as a QR image survive the OpenCV detector
at the same resolution WebRTC would deliver.
"""
from __future__ import annotations

import queue
import threading
import time

import pytest

cv2 = pytest.importorskip("cv2")
qrcode = pytest.importorskip("qrcode")
np = pytest.importorskip("numpy")

from userbot_bale.vpn.transports.video_qr_transport import VideoQrTransport


class _StubVideoOut:
    def __init__(self, sink) -> None:
        self._sink = sink
    def push_frame(self, frame_rgb24) -> None:
        # Convert RGB→BGR and deliver straight to the "peer".
        bgr = frame_rgb24[:, :, ::-1].copy()
        self._sink(bgr)


class _StubSession:
    def __init__(self) -> None:
        self._on_frame = None
        self._out = None

    def publish_video(self, w, h):
        # The pushed frame goes into our own on-frame callback, simulating
        # a lossless local loopback (no video codec in the middle).
        def deliver(bgr):
            if self._on_frame is not None:
                self._on_frame(bgr)
        self._out = _StubVideoOut(deliver)
        return self._out

    def on_video_frame(self, cb):
        self._on_frame = cb


def test_qr_roundtrip_via_transport():
    sess = _StubSession()
    t = VideoQrTransport(sess, width=480, height=480, fps=40)
    try:
        payload = b"vpn-frame:" + bytes(range(0, 100))
        t.send_bytes(payload)
        deadline = time.monotonic() + 3.0
        got = None
        while time.monotonic() < deadline:
            got = t.recv_bytes(timeout=0.2)
            if got is not None:
                break
        assert got == payload, f"roundtrip failed: got {got!r}"
    finally:
        t.close()


def test_qr_rejects_oversize():
    sess = _StubSession()
    t = VideoQrTransport(sess, fps=40)
    try:
        with pytest.raises(ValueError):
            t.send_bytes(b"x" * (t.mtu + 1))
    finally:
        t.close()


def test_qr_dedup_on_repeat():
    sess = _StubSession()
    t = VideoQrTransport(sess, width=480, height=480, fps=40)
    try:
        t.send_bytes(b"dedup-me")
        # First arrival.
        first = None
        deadline = time.monotonic() + 3.0
        while first is None and time.monotonic() < deadline:
            first = t.recv_bytes(timeout=0.2)
        assert first == b"dedup-me"
        # Subsequent repeats (every tick the tx thread re-pushes the
        # last frame) must not enqueue duplicates.
        time.sleep(0.5)
        extra = t.recv_bytes(timeout=0.1)
        assert extra is None, f"unexpected duplicate: {extra!r}"
    finally:
        t.close()
