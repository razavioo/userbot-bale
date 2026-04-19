"""
mitmproxy addon: Extract Bale RPCs from a recorded session (.mitm file).

Usage:
    mitmdump -r captures/bale-<ts>.mitm -s scripts/capture/extract_rpcs.py

Writes extracted payloads to captures/extracted-<ts>/<service>__<method>.<req|res>.bin.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from mitmproxy import ctx, http, websocket

_WS_HOST = "next-ws.bale.ai"
_OUT_BASE = Path("captures")


class RpcExtractor:
    def __init__(self):
        self.session_id = f"extracted-{int(time.time())}"
        self.out_dir = _OUT_BASE / self.session_id
        self.counter = 0

    def load(self, loader):
        loader.add_option(
            "outdir", str, "", "Directory to save extracted RPCs"
        )

    def configure(self, updated):
        if "outdir" in updated and ctx.options.outdir:
            self.out_dir = Path(ctx.options.outdir)
        
        if not self.out_dir.exists():
            self.out_dir.mkdir(parents=True, exist_ok=True)
            ctx.log.info(f"Extracted RPCs will be saved to: {self.out_dir}")

    def response(self, flow: http.HTTPFlow) -> None:
        """Handle gRPC-Web / HTTP POST RPCs."""
        if flow.request.host != _WS_HOST or flow.request.method != "POST":
            return
        
        self.counter += 1
        # Example path: /bale.users.v1.Users/SearchContacts
        rpc_path = flow.request.path.strip("/")
        rpc_name = rpc_path.replace("/", "__")
        
        prefix = self.out_dir / f"{self.counter:03d}_rpc_{rpc_name}"
        
        suffix_req = "req.bin"
        suffix_res = "res.bin"
        
        prefix.with_suffix(f".{suffix_req}").write_bytes(flow.request.content or b"")
        if flow.response:
            prefix.with_suffix(f".{suffix_res}").write_bytes(flow.response.content or b"")
            
        print(f"Extracted RPC: {rpc_path} -> {prefix.name}")

    def websocket_message(self, flow: http.HTTPFlow) -> None:
        """Handle WebSocket RPCs (if mitmproxy captured them)."""
        if not flow.websocket:
            return
        
        # mitmproxy records messages in flow.websocket.messages
        # When running with -r, we might see the whole list at once or as they come.
        # However, many versions of mitmproxy don't easily allow re-traversing
        # WS messages in an addon without some hacks if they were already processed.
        # But we'll try to handle the message stream.
        
        msg = flow.websocket.messages[-1]
        if not msg.content:
            return
            
        self.counter += 1
        who = "cl" if msg.from_client else "sv"
        name = self.out_dir / f"{self.counter:03d}_ws_{who}_{len(msg.content)}B.bin"
        name.write_bytes(msg.content)

addons = [RpcExtractor()]
