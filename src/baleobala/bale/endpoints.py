"""
Bale endpoint bootstrap.

The Bale Android app fetches http://ep.bale.ai/ep/endpoints-android.json on
startup to learn where to connect. The format is Actor-Platform-style:

    tls@<sha256_hex>://<hostname>@<ip>:<port>#<id>
    tcp@<sha256_hex>://<hostname>@<ip>:<port>#<id>

- `tls` / `tcp`: transport scheme.
- `<sha256_hex>`: 32-byte hex SHA-256 pin of the expected server public key.
- `<hostname>`: what the client uses for SNI and logging.
- `<ip>`: hard-coded fallback address in case DNS is blocked.
- `<port>`: typically 443.
- `<id>`: endpoint identifier (1013, 1014, ...).

This module fetches that file and exposes it as a parsed list. The
downstream transport (still TODO) uses these endpoints to connect and
perform the MTProto-style auth-key handshake.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List
from urllib.request import Request, urlopen

ENDPOINTS_URL = "http://ep.bale.ai/ep/endpoints-android.json"
HASHES_URL = "http://hash.bale.ai/hashes-android.json"
CONFIG_URL = "https://assets.bale.ai/configs.json"

# User-Agent the app sends. Using the same string avoids trivial
# blocking by the endpoint service; it's not a secret, the APK ships
# it as a hard-coded literal.
USER_AGENT = "okhttp/4.12.0"

_ENDPOINT_RE = re.compile(
    r"^(?P<scheme>tls|tcp)@(?P<pin>[0-9a-f]{64})"
    r"://(?P<host>[^@]+)@(?P<ip>[^:]+):(?P<port>\d+)"
    r"(?:#(?P<id>\d+))?$"
)


@dataclass(frozen=True)
class Endpoint:
    scheme: str       # "tls" or "tcp"
    pin: str          # sha256 hex of the server pubkey
    host: str         # SNI hostname
    ip: str           # fallback IP address
    port: int
    id: int


def parse_endpoints(body: str) -> List[Endpoint]:
    """Parse the endpoints bootstrap response (newline-delimited)."""
    out: List[Endpoint] = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line == "r":  # "r" is a leading marker in the live file
            continue
        m = _ENDPOINT_RE.match(line)
        if not m:
            continue
        out.append(Endpoint(
            scheme=m["scheme"],
            pin=m["pin"],
            host=m["host"],
            ip=m["ip"],
            port=int(m["port"]),
            id=int(m["id"]) if m["id"] else 0,
        ))
    return out


def fetch_endpoints(timeout: float = 5.0) -> List[Endpoint]:
    """Fetch and parse the current endpoint list from Bale's bootstrap."""
    req = Request(ENDPOINTS_URL, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    return parse_endpoints(body)
