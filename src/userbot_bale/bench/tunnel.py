"""Tunnel throughput benchmark.

Measures sustained send throughput, round-trip latency (p50/p95/p99),
and packet-loss fraction over an established userbot-bale VPN tunnel by
sending a stream of fixed-size UDP probes to a well-known echo endpoint
through the TUN interface.

Usage (CLI):
    userbot-bale bench tunnel \\
        --target 10.77.0.1 \\          # gateway or any reachable IP inside the tunnel
        --port 4444 \\
        --duration 60 \\
        --concurrency 4 \\
        --frame-size 1024 \\
        --out results.json

The benchmark does NOT set up the tunnel — run `userbot-bale vpn up` first.
The --target must be reachable through the VPN interface.

Output JSON schema:
    {
      "duration_secs": float,
      "concurrency": int,
      "frame_size_bytes": int,
      "sent": int,
      "received": int,
      "loss_fraction": float,        // 0.0 = no loss
      "throughput_kbps": float,      // kilobytes per second (send side)
      "rtt_p50_ms": float,
      "rtt_p95_ms": float,
      "rtt_p99_ms": float,
      "baseline_kbps": float | null, // from --baseline-file, or null
      "regression": bool | null,     // true if < 80 % of baseline
    }
"""

from __future__ import annotations

import json
import os
import socket
import statistics
import threading
import time
from pathlib import Path
from typing import Any


_ECHO_MAGIC = b"BBBE"  # userbot-bale bench echo


def _echo_server(sock: socket.socket, stop: threading.Event) -> None:
    """UDP echo server — reflect every datagram back to sender."""
    sock.settimeout(0.2)
    while not stop.is_set():
        try:
            data, addr = sock.recvfrom(65535)
            sock.sendto(data, addr)
        except socket.timeout:
            pass
        except OSError:
            break


def run_bench(
    *,
    target: str,
    port: int,
    duration: float,
    concurrency: int,
    frame_size: int,
    baseline_kbps: float | None = None,
) -> dict[str, Any]:
    """Run the benchmark and return the result dict."""
    # Payload: [MAGIC(4)] [send_ts_ns(8)] [padding to fill frame_size]
    header_size = len(_ECHO_MAGIC) + 8  # 12 bytes
    frame_size = max(frame_size, header_size)
    padding = bytes(frame_size - header_size)

    sent_total = 0
    recv_total = 0
    rtts_ms: list[float] = []
    lock = threading.Lock()
    stop = threading.Event()

    def _worker() -> None:
        nonlocal sent_total, recv_total
        # Use a single socket so the echo server reflects to the correct port.
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("", 0))
        sock.settimeout(0.05)

        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            ts_ns = time.time_ns()
            payload = _ECHO_MAGIC + ts_ns.to_bytes(8, "big") + padding
            try:
                sock.sendto(payload, (target, port))
            except OSError:
                continue
            with lock:
                sent_total += 1

            # Drain all available echoes (non-blocking; best-effort).
            recv_deadline = time.monotonic() + 0.05
            while time.monotonic() < recv_deadline:
                try:
                    data, _ = sock.recvfrom(65535)
                    if data[:4] == _ECHO_MAGIC and len(data) >= header_size:
                        send_ns = int.from_bytes(data[4:12], "big")
                        rtt_ms = (time.time_ns() - send_ns) / 1e6
                        with lock:
                            recv_total += 1
                            rtts_ms.append(rtt_ms)
                except socket.timeout:
                    break
                except OSError:
                    break

        sock.close()

    t0 = time.monotonic()
    workers = [threading.Thread(target=_worker, daemon=True) for _ in range(concurrency)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    elapsed = time.monotonic() - t0

    loss = 1.0 - (recv_total / sent_total) if sent_total else 0.0
    throughput_kbps = (sent_total * frame_size) / elapsed / 1024.0

    rtts_sorted = sorted(rtts_ms)
    def _pct(p: float) -> float:
        if not rtts_sorted:
            return 0.0
        idx = int(len(rtts_sorted) * p / 100.0)
        idx = min(idx, len(rtts_sorted) - 1)
        return rtts_sorted[idx]

    regression = None
    if baseline_kbps is not None:
        regression = throughput_kbps < baseline_kbps * 0.80

    return {
        "duration_secs": round(elapsed, 2),
        "concurrency": concurrency,
        "frame_size_bytes": frame_size,
        "sent": sent_total,
        "received": recv_total,
        "loss_fraction": round(loss, 4),
        "throughput_kbps": round(throughput_kbps, 1),
        "rtt_p50_ms": round(_pct(50), 2),
        "rtt_p95_ms": round(_pct(95), 2),
        "rtt_p99_ms": round(_pct(99), 2),
        "baseline_kbps": baseline_kbps,
        "regression": regression,
    }


def run_echo_server(port: int, bind: str = "0.0.0.0") -> None:
    """Start a blocking UDP echo server (used on the relay/exit-node side)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((bind, port))
    stop = threading.Event()
    try:
        _echo_server(sock, stop)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        sock.close()
