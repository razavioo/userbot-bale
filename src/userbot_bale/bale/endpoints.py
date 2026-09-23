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
downstream MTProto transport (`userbot_bale.bale.mtproto_backend`) uses
these endpoints to connect and perform the auth-key handshake.
"""

from __future__ import annotations

import logging
import re
import ssl
from dataclasses import dataclass
from typing import List
from urllib.request import Request, urlopen

ENDPOINTS_URL = "http://ep.bale.ai/ep/endpoints-android.json"
ENDPOINTS_HTTPS_URL = "https://ep.bale.ai/ep/endpoints-android.json"
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

_BOOTSTRAP_URLS = (ENDPOINTS_URL, ENDPOINTS_HTTPS_URL)

# Last known good bootstrap from a live fetch on 2026-04-19. This keeps the
# transport stack usable when Bale's bootstrap endpoint times out but the RPC
# fleet itself is still reachable.
_FALLBACK_ENDPOINTS_BODY = """r
tls@6d9ba5c5c665b0a7066682a05329f2a9c4c11aa08c9c548ebd33b9d06bf6e444://rpc-ssl-c002.bale.ai@2.189.68.117:443#1013
tcp@6d9ba5c5c665b0a7066682a05329f2a9c4c11aa08c9c548ebd33b9d06bf6e444://rpc-c002.bale.ai@2.189.68.106:443#1014
"""

log = logging.getLogger(__name__)


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


def _download_bootstrap(url: str, *, timeout: float, context: ssl.SSLContext) -> str:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    # The bootstrap list is public metadata; trust is enforced later by the
    # MTProto-layer pinning, so we tolerate Bale's HTTPS redirect chain even
    # when the local CA bundle does not validate it cleanly.
    with urlopen(req, timeout=timeout, context=context) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_endpoints(timeout: float = 5.0, *, allow_fallback: bool = True) -> List[Endpoint]:
    """Fetch and parse the current endpoint list from Bale's bootstrap.

    Bale's bootstrap endpoint is occasionally slower than the RPC servers it
    advertises, so we retry the direct HTTPS form and finally fall back to a
    bundled last-known-good snapshot unless strict behavior is requested.
    """
    ctx = ssl._create_unverified_context()
    last_error: Exception | None = None
    for url in _BOOTSTRAP_URLS:
        try:
            body = _download_bootstrap(url, timeout=timeout, context=ctx)
        except OSError as exc:
            last_error = exc
            continue
        endpoints = parse_endpoints(body)
        if endpoints:
            return endpoints
        last_error = RuntimeError(f"Bale endpoint bootstrap from {url} returned no usable entries")

    if allow_fallback:
        fallback = parse_endpoints(_FALLBACK_ENDPOINTS_BODY)
        if fallback:
            if last_error is not None:
                log.warning(
                    "Bale endpoint bootstrap unavailable, using bundled fallback: %s",
                    last_error,
                )
            return fallback

    if last_error is not None:
        raise last_error
    raise RuntimeError("Bale endpoint bootstrap failed without a specific error")
