"""
Live test: listen for LiveKit-call-credentials updates from Bale.

Workflow:
  1. Connects to wss://next-ws.bale.ai/ws/ with the access_token JWT.
  2. Leaves the connection open, dumping every server-pushed update.
  3. When the user initiates a call in their browser (web.bale.ai),
     Bale pushes an update containing the LiveKit room URL + token.
  4. We decode it and print ready-to-use `baleobala bale-call` command.

Reads JWT from /tmp/bale_jwt.txt or the BALE_JWT env var. Never
commit the JWT.
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from baleobala.bale.ws_client import WsClient  # noqa: E402


_LIVEKIT_TOKEN_RE = re.compile(rb"eyJhbGciOi[A-Za-z0-9_\-.]+")
_LIVEKIT_URL_RE = re.compile(rb"wss://[a-zA-Z0-9./\-]+")


def _extract_livekit(buf: bytes) -> tuple[str | None, str | None]:
    """Greedy extract: find the longest JWT and the wss:// URL in the bytes.
    The update frame contains both as length-prefixed string fields; we
    don't bother parsing the enclosing protobuf, just match the payloads.
    """
    url = None
    token = None
    m = _LIVEKIT_URL_RE.search(buf)
    if m:
        url = m.group(0).decode("ascii").rstrip("0")  # strip trailing protobuf cruft
    m = _LIVEKIT_TOKEN_RE.search(buf)
    if m:
        token = m.group(0).decode("ascii")
    return url, token


def main() -> int:
    jwt = os.environ.get("BALE_JWT", "").strip()
    if not jwt:
        path = Path("/tmp/bale_jwt.txt")
        if path.exists():
            jwt = path.read_text().strip()
    if not jwt:
        print("no JWT: set BALE_JWT or /tmp/bale_jwt.txt", file=sys.stderr)
        return 2

    creds_found = {"url": None, "token": None}

    def on_update(resp) -> None:
        buf = resp.raw
        url, token = _extract_livekit(buf)
        if url and token and (url != creds_found["url"] or token != creds_found["token"]):
            creds_found["url"] = url
            creds_found["token"] = token
            print("\n" + "=" * 60)
            print("LIVEKIT CALL CREDENTIALS RECEIVED:")
            print(f"  url:   {url}")
            print(f"  token: {token[:60]}...")
            print("\nReady-to-run command:")
            print("  baleobala bale-call send "
                  f"--livekit-url '{url}' "
                  f"--livekit-token '{token}' "
                  "--text 'hello from baleobala'")
            print("=" * 60 + "\n")
        else:
            # Concise trace of every update
            print(f"update: {len(buf)}B head={buf[:24].hex()}")

    client = WsClient(jwt=jwt, on_update=on_update)
    client.start()
    print("connected to wss://next-ws.bale.ai/ws/")
    print("Waiting for updates. Place a call in web.bale.ai to trigger.")
    print("(Ctrl-C to stop.)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        client.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
