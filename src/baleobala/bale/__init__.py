"""
Bale integration package.

Modules:
    livekit_backend    AudioSink / AudioSource that push/pull PCM over
                       LiveKit rooms (standard WebRTC), working today
                       against any LiveKit URL+token pair.

    api                High-level RPC client for ai.bale.proto.* — the
                       Bale-gRPC-over-Nasim-MTProto layer that issues
                       the LiveKit token. Scaffolded with the exact
                       proto classes found in the APK; the transport
                       binding is left as a well-marked TODO because
                       the MTProto handshake needs a mitmproxy capture
                       to finalise.

    auth               Phone + SMS + JWT flow wrapper around
                       AuthOuterClass.RequestStartPhoneAuth /
                       RequestValidateCode. Same TODO as api: the RPC
                       dispatcher is ready, the wire layer is next.

Separation of concerns: the LiveKit backend is fully functional in
isolation — give it a URL and token and baleobala frames flow. That
lets the RE work on auth/api proceed without blocking the audio half.
"""

from baleobala.bale.api import BaleApiClient, LiveKitCredentials
from baleobala.bale.endpoints import Endpoint, fetch_endpoints
from baleobala.bale.livekit_backend import (
    LiveKitSink,
    LiveKitSource,
    LiveKitSession,
)
from baleobala.bale.protos import (
    CallCredentials, OutPeer, RequestStartLiveKitCall, parse_call_credentials,
)
from baleobala.bale.ws_client import WsClient

__all__ = [
    "BaleApiClient",
    "CallCredentials",
    "Endpoint",
    "LiveKitCredentials",
    "LiveKitSink",
    "LiveKitSource",
    "LiveKitSession",
    "OutPeer",
    "RequestStartLiveKitCall",
    "WsClient",
    "fetch_endpoints",
    "parse_call_credentials",
]
