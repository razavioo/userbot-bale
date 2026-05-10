"""Unit tests for baleobala.bench.tunnel.

The echo server and run_bench() are exercised over loopback so these
tests require no VPN interface and run in < 5 s total.
"""
from __future__ import annotations

import json
import socket
import threading
import time

import pytest

from baleobala.bench.tunnel import _ECHO_MAGIC, _echo_server, run_bench


def _start_echo_server(port: int) -> tuple[socket.socket, threading.Event]:
    """Bind a UDP echo server on 127.0.0.1:port and start it in a thread."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    stop = threading.Event()
    t = threading.Thread(target=_echo_server, args=(sock, stop), daemon=True)
    t.start()
    return sock, stop


def test_echo_server_reflects_datagrams() -> None:
    sock, stop = _start_echo_server(0)
    port = sock.getsockname()[1]
    try:
        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client.settimeout(1.0)
        payload = b"hello-bench"
        client.sendto(payload, ("127.0.0.1", port))
        data, _ = client.recvfrom(256)
        assert data == payload
        client.close()
    finally:
        stop.set()
        sock.close()


def test_run_bench_loopback_returns_sane_result() -> None:
    """Full run_bench() over loopback with a 1-second duration."""
    sock, stop = _start_echo_server(0)
    port = sock.getsockname()[1]
    try:
        result = run_bench(
            target="127.0.0.1",
            port=port,
            duration=1.0,
            concurrency=1,
            frame_size=128,
        )
    finally:
        stop.set()
        sock.close()

    assert result["sent"] > 0, "no packets sent"
    assert result["received"] > 0, "no echo replies received"
    assert 0.0 <= result["loss_fraction"] <= 1.0
    assert result["throughput_kbps"] > 0
    assert result["rtt_p50_ms"] > 0
    assert result["rtt_p95_ms"] >= result["rtt_p50_ms"]
    assert result["regression"] is None
    assert result["baseline_kbps"] is None


def test_run_bench_regression_flag_set_when_below_threshold() -> None:
    sock, stop = _start_echo_server(0)
    port = sock.getsockname()[1]
    try:
        result = run_bench(
            target="127.0.0.1",
            port=port,
            duration=0.5,
            concurrency=1,
            frame_size=64,
            baseline_kbps=999_999.0,  # impossibly high baseline → regression
        )
    finally:
        stop.set()
        sock.close()

    assert result["regression"] is True


def test_run_bench_no_regression_when_meets_threshold() -> None:
    sock, stop = _start_echo_server(0)
    port = sock.getsockname()[1]
    try:
        result = run_bench(
            target="127.0.0.1",
            port=port,
            duration=0.5,
            concurrency=1,
            frame_size=64,
            baseline_kbps=0.001,  # trivially low baseline → no regression
        )
    finally:
        stop.set()
        sock.close()

    assert result["regression"] is False


def test_run_bench_result_json_serialisable() -> None:
    sock, stop = _start_echo_server(0)
    port = sock.getsockname()[1]
    try:
        result = run_bench(
            target="127.0.0.1",
            port=port,
            duration=0.3,
            concurrency=1,
            frame_size=64,
        )
    finally:
        stop.set()
        sock.close()

    body = json.dumps(result)
    parsed = json.loads(body)
    assert parsed["sent"] == result["sent"]
