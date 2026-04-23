"""
Nasim-MTProto transport (in-progress port).

Bale's Android client speaks an Actor-Platform-derived variant of MTProto
over TCP. The transport is split into small layers so each can be tested
independently:

    endpoint.py   TCP + TLS-pinned connection to one Endpoint.
    framing.py    Raw-frame read/write on top of a connected endpoint.
    authkey.py    Auth-key models + live negotiation boundary.
    session.py    Session state + framed-session codec + classification.
    rpc.py        Request→future dispatcher + update subscription.
    store.py      Durable persisted MTProto session state.

The endpoint layer is production-ready and live-tested against Bale's
real servers (TLS pin verified). Layers marked PENDING capture require
a mitmproxy trace of the Bale Android client to finalise; see
docs/BALE_RE_NOTES.md for the capture workflow.
"""

from baleobala.bale.mtproto.endpoint import (
    EndpointConnection,
    connect,
)
from baleobala.bale.mtproto.authkey import (
    AuthKeyNegotiationNotReady,
    AuthKeyNegotiator,
    BaleP256DhHeuristicHandshakeCodec,
    HandshakeCodec,
    HandshakeTranscript,
    MtprotoAuthKey,
    PlaceholderHandshakeCodec,
    ServerHandshakeMaterial,
)
from baleobala.bale.mtproto.framing import Frame, read_frame, write_frame
from baleobala.bale.mtproto.rpc import MtpRpcClient
from baleobala.bale.mtproto.session import (
    InboundEnvelope,
    MtprotoSession,
    MtprotoSessionState,
    PlainSessionCodec,
    SessionCodec,
)
from baleobala.bale.mtproto.store import MtprotoSessionStore, PersistedMtprotoSession

__all__ = [
    "AuthKeyNegotiationNotReady",
    "AuthKeyNegotiator",
    "BaleP256DhHeuristicHandshakeCodec",
    "EndpointConnection",
    "Frame",
    "HandshakeCodec",
    "HandshakeTranscript",
    "InboundEnvelope",
    "MtpRpcClient",
    "MtprotoAuthKey",
    "MtprotoSession",
    "MtprotoSessionState",
    "MtprotoSessionStore",
    "PlaceholderHandshakeCodec",
    "PersistedMtprotoSession",
    "PlainSessionCodec",
    "SessionCodec",
    "ServerHandshakeMaterial",
    "connect",
    "read_frame",
    "write_frame",
]
