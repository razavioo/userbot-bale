"""Sanity checks on the scaffolded Bale API surface."""

from __future__ import annotations

import pytest

from baleobala.bale.api import BaleApiClient, LiveKitCredentials
from baleobala.bale.auth import BaleAuth


def test_livekit_credentials_shape() -> None:
    c = LiveKitCredentials(url="wss://x", token="t", room="r", identity="i")
    assert c.url == "wss://x"


def test_api_stub_points_to_docs() -> None:
    client = BaleApiClient()
    with pytest.raises(NotImplementedError) as e:
        client.fetch_livekit_credentials(peer_id=1)
    assert "docs/BALE" in str(e.value)


def test_auth_stub_points_to_docs() -> None:
    auth = BaleAuth()
    with pytest.raises(NotImplementedError) as e:
        auth.start_phone_auth("+989120000000")
    assert "BALE_RE_NOTES" in str(e.value) or "transport" in str(e.value)
