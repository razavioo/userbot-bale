"""
High-level `bale_call` helpers: orchestrate a Bale LiveKit call end-to-end.

Two entry points that accept either a complete credentials bundle (works
today) or a BaleApiClient to fetch one (works once the RPC transport is
ready — see api.py).
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from userbot_bale.bale.api import BaleApiClient, LiveKitCredentials

log = logging.getLogger(__name__)


@contextmanager
def livekit_session(
    *,
    url: str,
    token: str,
    identity: str = "userbot-bale",
) -> Iterator[LiveKitSession]:
    """
    Connect to a LiveKit room and tear it down cleanly.

    Example:
        with livekit_session(url=..., token=...) as session:
            with Transmitter(sink=session.sink()) as tx:
                tx.send("hello from bale")
    """
    from userbot_bale.bale.livekit_backend import LiveKitSession

    session = LiveKitSession(url=url, token=token, identity=identity)
    session.start()
    try:
        yield session
    finally:
        session.stop()


def fetch_credentials(
    client: BaleApiClient,
    *,
    peer_id: int,
    video: bool = False,
) -> LiveKitCredentials:
    """Thin shim so CLI doesn't import api directly — keeps the import
    surface small when the transport layer is not installed."""
    return client.fetch_livekit_credentials(peer_id=peer_id, video=video)
