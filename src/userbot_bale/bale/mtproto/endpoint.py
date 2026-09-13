"""
TCP / TLS connection to a Bale server endpoint.

An `Endpoint` from userbot_bale.bale.endpoints carries:

    scheme: "tls" | "tcp"
    pin:    32 bytes hex — the MTProto server RSA pubkey fingerprint
            (Actor Platform convention), NOT a TLS cert pin. It's
            consumed later during the auth-key handshake to identify
            which server key the handshake expects.
    host:   SNI hostname
    ip:     fallback dotted-quad
    port:   usually 443

Confirmed empirically against rpc-ssl-c002.bale.ai on 2026-04-19:
SHA-256 of the cert DER, SPKI DER, and RSA modulus all differ from the
endpoint pin. The TLS cert is signed by a regular public CA; the pin's
role is MTProto-layer server-identity, not TLS-layer trust.

So at this layer:
  * For `tls`, we complete a standard TLS handshake with a real CA
    chain check OFF and hostname check OFF, because the cert is not
    the trust anchor. Transport integrity is the MTProto encryption.
  * For `tcp`, raw socket.

We do DNS resolution via `host`, and if DNS fails we fall back to
connecting directly to `ip` (the reason Bale ships the IP inline: it
works when DNS is blocked).
"""

from __future__ import annotations

import logging
import socket
import ssl
from dataclasses import dataclass

from userbot_bale.bale.endpoints import Endpoint

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 10.0


class PinVerificationError(RuntimeError):
    """Raised when the server's presented public key doesn't match the
    endpoint's declared SHA-256 pin. Security-critical: never log the
    server bytes in a way that might leak, never retry a failing pin."""


@dataclass
class EndpointConnection:
    """A live connection ready to carry MTProto frames.

    `stream` is either a plain `socket.socket` (tcp scheme) or an
    `ssl.SSLSocket` (tls scheme); both support `recv`/`sendall`/`close`
    with the same interface.
    """
    endpoint: Endpoint
    stream: socket.socket

    def close(self) -> None:
        try:
            self.stream.close()
        except OSError:
            pass


def _resolve(host: str, fallback_ip: str) -> str:
    """Return an IP to connect to. Try DNS first, fall back to the
    hard-coded address so the bootstrap still works when DNS is
    blocked or poisoned (the reason Bale ships the IP in the first
    place)."""
    try:
        return socket.gethostbyname(host)
    except OSError as e:
        log.info("DNS for %s failed (%s); using fallback %s",
                 host, e, fallback_ip)
        return fallback_ip


def connect(endpoint: Endpoint) -> EndpointConnection:
    """Open a connection to `endpoint`.

    For `tls`: standard TLS handshake with `check_hostname=False` and
    `verify_mode=CERT_NONE`. The TLS cert is NOT the trust anchor;
    trust flows through the MTProto auth-key exchange which uses
    `endpoint.pin` as the server RSA pubkey fingerprint.

    For `tcp`: raw socket.

    The `endpoint.pin` is returned unchanged inside the
    `EndpointConnection` for the upstream handshake layer to consume.
    """
    ip = _resolve(endpoint.host, endpoint.ip)
    raw = socket.create_connection(
        (ip, endpoint.port), timeout=CONNECT_TIMEOUT_S,
    )

    if endpoint.scheme == "tcp":
        log.info("connected tcp %s:%d", endpoint.host, endpoint.port)
        return EndpointConnection(endpoint=endpoint, stream=raw)

    if endpoint.scheme != "tls":
        raw.close()
        raise ValueError(f"unknown scheme: {endpoint.scheme}")

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        sslsock = ctx.wrap_socket(raw, server_hostname=endpoint.host)
    except Exception:
        raw.close()
        raise
    log.info("connected tls %s:%d", endpoint.host, endpoint.port)
    return EndpointConnection(endpoint=endpoint, stream=sslsock)
