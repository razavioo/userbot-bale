from __future__ import annotations

import socket

from baleobala.control.probe import probe_endpoint


def test_probe_endpoint_connects_to_tcp_listener() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    host, port = listener.getsockname()[:2]
    try:
        result = probe_endpoint(f"{host}:{port}")
        assert result.ok
        assert result.kind == "tcp"
    finally:
        listener.close()


def test_probe_endpoint_reports_missing_endpoint() -> None:
    result = probe_endpoint(None)
    assert not result.ok
    assert result.detail == "no endpoint"
