"""Live endpoint connection test. Skips when the Bale servers aren't reachable."""

from __future__ import annotations

import socket

import pytest

from baleobala.bale.endpoints import fetch_endpoints
from baleobala.bale.mtproto import connect


def _network_ok() -> bool:
    try:
        socket.create_connection(("ep.bale.ai", 80), timeout=3).close()
        return True
    except OSError:
        return False


@pytest.mark.skipif(not _network_ok(), reason="no network to ep.bale.ai")
def test_live_tls_connect() -> None:
    endpoints = fetch_endpoints()
    tls = [e for e in endpoints if e.scheme == "tls"]
    assert tls, "no TLS endpoint advertised"
    conn = connect(tls[0])
    try:
        assert conn.endpoint.host.endswith(".bale.ai")
        assert conn.stream.fileno() > 0
    finally:
        conn.close()
