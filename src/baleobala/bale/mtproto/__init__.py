"""
Nasim-MTProto transport (in-progress port).

Bale's Android client speaks an Actor-Platform-derived variant of MTProto
over TCP. The transport is split into small layers so each can be tested
independently:

    endpoint.py   TCP + TLS-pinned connection to one Endpoint.
    framing.py    Raw-frame read/write on top of a connected endpoint.
    authkey.py    [PENDING capture] DH-based auth-key negotiation.
    session.py    [PENDING capture] Auth-key lifecycle + persistence.
    rpc.py        [PENDING capture] Request→future dispatcher + updates.

The endpoint layer is production-ready and live-tested against Bale's
real servers (TLS pin verified). Layers marked PENDING capture require
a mitmproxy trace of the Bale Android client to finalise; see
docs/BALE_RE_NOTES.md for the capture workflow.
"""

from baleobala.bale.mtproto.endpoint import (
    EndpointConnection,
    connect,
)

__all__ = [
    "EndpointConnection",
    "connect",
]
