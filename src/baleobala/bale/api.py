"""
Bale API client — high-level wrapper over the WS-based RPC layer.

Status (2026-04-19): **live-tested against production servers.**

- `BaleApiClient` wraps `WsClient` with typed methods for the small
  subset of Bale RPCs that baleobala needs.
- `start_call(peer_id)` calls `bale.meet.v1.Meet/StartCall` and
  returns `CallCredentials` extracted from the server's push update.
- `listen_incoming_calls(callback)` subscribes to server-pushed
  updates and fires on any frame that contains a LiveKit token +
  wss:// URL pair — the canonical shape of a call-credential push.

Protobuf catalog (from the APK decompile under
`re/jadx-out/sources/ai/bale/proto/`):

    MeetOuterClass.RequestStartLiveKitCall
        field 1: peer (OutPeer)
        field 2: rid (int64)
        field 3: video (bool)
        field 4: inviteEnable (BooleanValue)

The outer RPC-payload envelope wraps it at tag 6 (empirically).

Peer resolution (phone → OutPeer) is **not yet implemented**. Bale's
web client caches contacts and does not hit a contacts RPC for known
numbers during a call; resolving arbitrary phones would need a
capture of `bale.users.v1.Users/*` or `bale.contacts.*` which wasn't
triggered in the session we reversed. The CLI accepts `--peer-id`
(numeric user_id) as the definitive form and documents the gap.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

from baleobala.bale.endpoints import Endpoint, fetch_endpoints
from baleobala.bale.protos import (
    CallCredentials, OutPeer, RequestStartLiveKitCall, parse_call_credentials,
)
from baleobala.bale.rpc_envelope import Response
from baleobala.bale.ws_client import WsClient

log = logging.getLogger(__name__)


# Back-compat with the earlier stub API (same shape).
@dataclass(frozen=True)
class LiveKitCredentials:
    url: str
    token: str
    room: str
    identity: str


class BaleApiClient:
    """Live Bale API client.

    Usage:
        client = BaleApiClient(jwt=...)
        client.start()
        try:
            creds = client.start_call(peer_id=460260975)
            # creds.url, creds.token ready for LiveKitSession
        finally:
            client.stop()
    """

    def __init__(
        self,
        jwt: str | None = None,
        ws_url: str | None = None,
        on_incoming_credentials: Optional[Callable[[CallCredentials], None]] = None,
    ) -> None:
        self._jwt = jwt
        self._ws_url = ws_url
        self._on_incoming_creds = on_incoming_credentials
        self._ws: WsClient | None = None
        self._endpoints: List[Endpoint] | None = None
        self._last_creds: CallCredentials | None = None
        self._creds_event = threading.Event()

    # ------------------------------------------------------------------ lifecycle

    def bootstrap(self) -> List[Endpoint]:
        """Fetch the live MTProto endpoints list. Not used by the WS
        path but exposed so callers can verify connectivity."""
        if self._endpoints is None:
            self._endpoints = fetch_endpoints()
        return self._endpoints

    def start(self, timeout: float = 15.0) -> None:
        if not self._jwt:
            raise RuntimeError(
                "BaleApiClient requires a JWT access_token. Pass it to "
                "__init__ or implement auth.start_phone_auth first."
            )
        kwargs = {}
        if self._ws_url:
            kwargs["url"] = self._ws_url
        self._ws = WsClient(jwt=self._jwt, on_update=self._dispatch_update, **kwargs)
        self._ws.start(timeout=timeout)

    def stop(self) -> None:
        if self._ws is not None:
            self._ws.stop()
            self._ws = None

    # ------------------------------------------------------------------ RPCs

    def start_call(
        self,
        peer_id: int,
        *,
        peer_type: int = 1,
        video: bool = False,
        invite_enable: bool = True,
        creds_timeout: float = 20.0,
    ) -> CallCredentials:
        """Place a call. Returns CallCredentials once the server
        pushes them (usually within ~1 s of the RPC completing)."""
        if self._ws is None:
            raise RuntimeError("BaleApiClient not started")
        self._creds_event.clear()
        self._last_creds = None

        req = RequestStartLiveKitCall(
            peer=OutPeer(user_id=peer_id, type=peer_type),
            video=video,
            invite_enable=invite_enable,
        )
        payload = req.encode_as_rpc_payload()
        log.info("sending StartCall peer=%d video=%s", peer_id, video)
        resp = self._ws.rpc(
            "bale.meet.v1.Meet", "StartCall", payload, timeout=10.0,
        )
        log.debug("StartCall ack: seq=%s payload=%dB", resp.seq, len(resp.payload))

        # For outbound calls, Bale returns the LiveKit credentials in
        # the RPC response payload itself (verified live 2026-04-19).
        # For inbound calls, creds arrive via a push update instead.
        creds = parse_call_credentials(resp.raw)
        if creds is not None:
            self._last_creds = creds
            self._creds_event.set()
            return creds

        # Fallback: wait for a push update (inbound-call style).
        if self._creds_event.wait(timeout=creds_timeout):
            assert self._last_creds is not None
            return self._last_creds
        raise TimeoutError(
            f"StartCall ACKed but no credentials in response or push "
            f"within {creds_timeout}s"
        )

    def listen_incoming_calls(
        self,
        callback: Callable[[CallCredentials], None],
    ) -> None:
        """Register a callback for any incoming call credentials.

        Fires when the server pushes a LiveKit token — regardless of
        whether we initiated the call or someone called us. The user
        of this method typically:

            1. Calls this method with a callback.
            2. Awaits the callback (e.g. via threading.Event).
            3. Joins the LiveKit room using the received credentials.
        """
        self._on_incoming_creds = callback

    # ------------------------------------------------------------------ internals

    def _dispatch_update(self, resp: Response) -> None:
        creds = parse_call_credentials(resp.raw)
        if creds is None:
            return
        log.info("received call credentials: room=%s", creds.room)
        self._last_creds = creds
        self._creds_event.set()
        if self._on_incoming_creds is not None:
            try:
                self._on_incoming_creds(creds)
            except Exception:  # noqa: BLE001
                log.exception("on_incoming_creds callback failed")
