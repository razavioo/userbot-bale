"""
mitmproxy addon: stream WebSocket messages to captures/ws-live/.

Writes one file per frame in arrival order so we can inspect traffic
without waiting for the WS to close (which mitmproxy needs to serialise
the flow to .mitm). Also mirrors the gRPC-Web HTTP POSTs for
completeness.
"""

from __future__ import annotations

import os
import time
from mitmproxy import ctx, http, websocket

OUT = "captures/ws-live"
os.makedirs(OUT, exist_ok=True)

_counter = {"n": 0}


def websocket_message(flow: http.HTTPFlow) -> None:
    if not flow.websocket or not flow.websocket.messages:
        return
    m = flow.websocket.messages[-1]
    n = _counter["n"]
    _counter["n"] = n + 1
    who = "cl" if m.from_client else "sv"
    name = f"{OUT}/{n:05d}_{who}_{int(m.timestamp*1000)%100000000}.bin"
    with open(name, "wb") as f:
        f.write(m.content)
    ctx.log.info(f"ws {who} {len(m.content)}B -> {name}")


def response(flow: http.HTTPFlow) -> None:
    if flow.request.host != "next-ws.bale.ai":
        return
    if flow.request.method != "POST":
        return
    n = _counter["n"]
    _counter["n"] = n + 1
    rpc = flow.request.path.strip("/").replace("/", "__")
    prefix = f"{OUT}/{n:05d}_rpc_{rpc}"
    with open(prefix + ".req.bin", "wb") as f:
        f.write(flow.request.content or b"")
    with open(prefix + ".res.bin", "wb") as f:
        f.write(flow.response.content or b"")
    with open(prefix + ".headers.txt", "w") as f:
        f.write(f"REQUEST {flow.request.method} {flow.request.pretty_url}\n")
        for k, v in flow.request.headers.items():
            f.write(f"  {k}: {v}\n")
        f.write(f"\nRESPONSE {flow.response.status_code}\n")
        for k, v in flow.response.headers.items():
            f.write(f"  {k}: {v}\n")
    ctx.log.info(f"rpc {flow.request.path} -> {prefix}.*")
