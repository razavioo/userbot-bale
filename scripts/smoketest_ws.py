"""
Live smoketest: connect to wss://next-ws.bale.ai/ws/ with a captured
access_token JWT, send a no-op RPC, and print the response.

Reads the JWT from /tmp/bale_jwt.txt (never committed) or from the
BALE_JWT env var. Never commit the JWT.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from userbot_bale.bale.ws_client import WsClient  # noqa: E402


def main() -> int:
    jwt = os.environ.get("BALE_JWT", "").strip()
    if not jwt:
        path = Path("/tmp/bale_jwt.txt")
        if path.exists():
            jwt = path.read_text().strip()
    if not jwt:
        print("no JWT: set BALE_JWT or /tmp/bale_jwt.txt", file=sys.stderr)
        return 2

    client = WsClient(jwt=jwt)
    client.start()
    print("connected; sending GetOngoingCalls...")
    try:
        resp = client.rpc(
            "bale.meet.v1.Meet", "GetOngoingCalls", b"", timeout=15,
        )
        print(f"GetOngoingCalls response: {len(resp.payload)} bytes")
        print(f"  seq echoed: {resp.seq}")
        print(f"  payload head: {resp.payload[:64].hex()}")
        time.sleep(1)
    finally:
        client.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
