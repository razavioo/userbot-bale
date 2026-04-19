"""
Bale API client — high-level wrapper over ai.bale.proto.* RPCs.

Status: **partial**. The live endpoint bootstrap works (see
``baleobala.bale.endpoints.fetch_endpoints``). What is NOT yet
implemented is the Nasim-MTProto auth-key handshake on top of those
endpoints; that needs a live mitmproxy capture and a port of the
handshake from `ir.nasim.core.runtime.mtproto.*`. See
docs/BALE_RE_NOTES.md.

What works today
----------------
`fetch_livekit_credentials(...)` is a stub that raises NotImplementedError
with a clear message pointing the caller at the `--livekit-url` /
`--livekit-token` escape hatch on the CLI. This escape hatch lets the
user run the full baleobala ↔ LiveKit audio loop while the RPC layer is
being built out.

The dataclass returned by fetch_livekit_credentials is the shape the
completed implementation will match, so downstream code (the CLI, the
LiveKit backend) doesn't need to change when the transport lands.

Protobuf surface (verified from APK)
------------------------------------
Auth:
    ai.bale.proto.AuthOuterClass.RequestStartPhoneAuth
    ai.bale.proto.AuthOuterClass.RequestValidateCode
    ai.bale.proto.AuthOuterClass.RequestSignUp
    ai.bale.proto.AuthOuterClass.RequestGetJWTToken
    ai.bale.proto.AuthOuterClass.ResponseAuth
    ai.bale.proto.AuthOuterClass.ResponseGetJWTToken

Calls:
    ai.bale.proto.MeetOuterClass.RequestStartLiveKitCall
        fields: peer (OutPeer), rid (int64), video (bool),
                inviteEnable (BooleanValue)
    ai.bale.proto.MeetOuterClass.RequestAcceptCall
    ai.bale.proto.MeetOuterClass.RequestReceiveCall
    ai.bale.proto.MeetOuterClass.RequestJoinGroupCall
    ai.bale.proto.MeetOuterClass.RequestLeaveGroupCall
    ai.bale.proto.MeetOuterClass.RequestGetWssURL
    ai.bale.proto.MeetOuterClass.ResponseCall
    ai.bale.proto.MeetOuterClass.ResponseGetWssURL
    ai.bale.proto.MeetStruct.GroupCall
    ai.bale.proto.MeetStruct.SipCall
    ai.bale.proto.MeetOuterClass.UpdateCallStatusChanged

Transport:
    ir.nasim.core.runtime.mtproto.ConnectionEndpoint (endpoint struct)
    ir.nasim.core.network.util.ConnectionEndpoints (list of endpoints)
    ir.nasim.core.network.sslpinning.*                (TLS pinning)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from baleobala.bale.endpoints import Endpoint, fetch_endpoints


@dataclass(frozen=True)
class LiveKitCredentials:
    """Shape of what RequestStartLiveKitCall / ResponseCall returns.

    The actual ResponseCall proto likely carries more fields (call id,
    peer capabilities, recording options). This dataclass narrows to
    what baleobala's LiveKit backend actually consumes.
    """

    url: str           # LiveKit SFU WSS URL (likely wss://<host>/rtc)
    token: str         # LiveKit JWT, issued server-side by Bale
    room: str          # room/session name
    identity: str      # our participant identity in the room


class BaleApiClient:
    """
    Placeholder for the Bale API client. Construction is cheap; all
    RPCs raise NotImplementedError with an explanatory message that
    references the path through the decompile to complete them.
    """

    def __init__(self, jwt_token: str | None = None) -> None:
        self._jwt = jwt_token
        self._endpoints: List[Endpoint] | None = None

    def bootstrap(self) -> List[Endpoint]:
        """Fetch and cache the live Bale endpoint list. Cheap — a
        single HTTP GET against http://ep.bale.ai/."""
        if self._endpoints is None:
            self._endpoints = fetch_endpoints()
        return self._endpoints

    def fetch_livekit_credentials(
        self,
        *,
        peer_id: int,
        video: bool = False,
        invite_enable: bool = False,
    ) -> LiveKitCredentials:
        """
        Call ai.bale.proto.MeetOuterClass.RequestStartLiveKitCall and
        return the LiveKit URL + token in the response.

        NOT YET IMPLEMENTED. To finish:
            1. Build the Nasim-MTProto transport (ConnectionEndpoint +
               auth-key exchange). See docs/BALE_RE_NOTES.md.
            2. Serialise RequestStartLiveKitCall with peer=OutPeer(peer_id),
               rid=random_int64(), video=<video>,
               inviteEnable=<invite_enable>.
            3. Await ResponseCall and map the LiveKit URL + token fields
               onto LiveKitCredentials.
        Until then, use the CLI `--livekit-url` / `--livekit-token`
        options to bypass this layer entirely.
        """
        raise NotImplementedError(
            "Bale RPC transport is not yet implemented. See "
            "docs/BALE_HEADLESS.md for the escape hatch "
            "(--livekit-url / --livekit-token) and "
            "docs/BALE_RE_NOTES.md for the RE plan to finish this."
        )
