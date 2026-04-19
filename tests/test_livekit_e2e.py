"""
End-to-end LiveKit round-trip test.

Requires a running LiveKit dev server on ws://localhost:7880 (docker run
--rm --network host livekit/livekit-server --dev --node-ip=127.0.0.1).
Skips if the server isn't reachable or livekit SDK isn't installed.

The test:
  1. Mints two JWTs (sender/receiver) for the same room using the dev key.
  2. Spins up a LiveKitSession on each side.
  3. Runs Transmitter on the sender, sends "hello bale over livekit".
  4. Waits for Receiver on the receiver to emit that exact text.
"""

from __future__ import annotations

import socket
import time

import pytest

pytest_plugins: list[str] = []

pytest.importorskip("ggwave")

try:
    from livekit import api  # type: ignore
    from livekit import rtc  # type: ignore
    _HAS = True
except ImportError:
    _HAS = False

from baleobala.codec import Protocol

LK_URL = "ws://127.0.0.1:7880"
LK_KEY = "devkey"
LK_SECRET = "secret"
ROOM = "baleobala-e2e"


def _livekit_reachable() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 7880), timeout=1):
            return True
    except OSError:
        return False


def _mint_token(identity: str) -> str:
    return (
        api.AccessToken(LK_KEY, LK_SECRET)
        .with_identity(identity)
        .with_grants(api.VideoGrants(
            room_join=True, room=ROOM,
            can_publish=True, can_subscribe=True,
        ))
        .to_jwt()
    )


@pytest.mark.timeout(60)
@pytest.mark.skipif(not _HAS, reason="livekit SDK not installed")
@pytest.mark.skipif(not _livekit_reachable(), reason="no LiveKit dev server")
def test_livekit_e2e_roundtrip() -> None:
    from baleobala.bale.livekit_backend import LiveKitSession
    from baleobala.transmitter import Transmitter
    from baleobala.receiver import Receiver

    rx_session = LiveKitSession(
        url=LK_URL, token=_mint_token("rx"), identity="rx",
    )
    tx_session = LiveKitSession(
        url=LK_URL, token=_mint_token("tx"), identity="tx",
    )
    rx_session.start()
    tx_session.start()
    # Allow a moment for track subscription to settle on both sides.
    time.sleep(2.0)

    got: list[str] = []
    rx = Receiver(
        source=rx_session.source(), protocol=Protocol.AUDIBLE_FAST,
        on_message=lambda m: got.append(m.text()),
    )
    rx.start()
    try:
        with Transmitter(
            sink=tx_session.sink(), protocol=Protocol.AUDIBLE_FAST,
        ) as tx:
            tx.send("hello bale over livekit")
        deadline = time.time() + 30
        while time.time() < deadline and not got:
            time.sleep(0.2)
    finally:
        rx.stop()
        tx_session.stop()
        rx_session.stop()

    assert got == ["hello bale over livekit"], f"got={got!r}"
