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

Peer resolution (phone → OutPeer) is **implemented** via `resolve_peer`. It
uses `SearchContacts` (server-side phone search) with a fallback to
`ImportContacts` if the search yields nothing. The CLI accepts
`--peer` (phone), `--peer-name`, and `--peer-id` to trigger these
paths.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

from baleobala.bale.endpoints import Endpoint, fetch_endpoints
from baleobala.bale.protos import (
    CallCredentials, OutPeer, PhoneToImport, RequestImportContacts,
    RequestSearchContacts, RequestStartLiveKitCall, ResolvedContact,
    parse_call_credentials, parse_import_contacts_response,
    parse_search_contacts_response,
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
        return self.fetch_livekit_credentials(
            peer_id,
            peer_type=peer_type,
            video=video,
            invite_enable=invite_enable,
            creds_timeout=creds_timeout,
        )

    def fetch_livekit_credentials(
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

    def import_contacts(
        self, phones: list[str | int], name_prefix: str = "baleobala",
    ) -> list[ResolvedContact]:
        """Resolve phone numbers to user_ids via bale.users.v1.Users/ImportContacts.

        Accepts phones in any of these forms:
          - E.164 with '+' prefix: "+989127479731"
          - digits-only string:    "989127479731"
          - int:                   989127479731

        Returns a list of ResolvedContact in the server's response
        order (same order as the input list for matched numbers;
        unmatched numbers are dropped by the server, so the output
        length may be shorter than the input length).
        """
        if self._ws is None:
            raise RuntimeError("BaleApiClient not started")
        normalized: list[int] = []
        for p in phones:
            if isinstance(p, str):
                p = p.strip().lstrip("+")
                p = int(p)
            normalized.append(int(p))

        phone_msgs = [
            PhoneToImport(phone_number=n, name=f"{name_prefix}-{n}")
            for n in normalized
        ]
        payload = RequestImportContacts(phones=phone_msgs).encode()
        log.info("ImportContacts phones=%s", normalized)
        resp = self._ws.rpc(
            "bale.users.v1.Users", "ImportContacts", payload, timeout=15.0,
        )
        contacts = parse_import_contacts_response(resp.payload or resp.raw)
        log.info("ImportContacts returned %d user(s)", len(contacts))
        # Pair each resolved user with the phone it came from, in order.
        paired: list[ResolvedContact] = []
        for i, c in enumerate(contacts):
            phone = normalized[i] if i < len(normalized) else 0
            paired.append(ResolvedContact(
                phone_number=phone, user_id=c.user_id,
                access_hash=c.access_hash, name=c.name,
            ))
        return paired

    def search_contacts(self, query: str) -> list[ResolvedContact]:
        """bale.users.v1.Users/SearchContacts — query by phone or name.

        Accepts any query string; typical use is a phone number in
        E.164 format ('+989...') or a display name fragment.
        """
        if self._ws is None:
            raise RuntimeError("BaleApiClient not started")
        payload = RequestSearchContacts(query=query).encode()
        log.info("SearchContacts query=%r", query)
        resp = self._ws.rpc(
            "bale.users.v1.Users", "SearchContacts", payload, timeout=10.0,
        )
        return parse_search_contacts_response(resp.payload or resp.raw)

    def resolve_peer(self, phone: str | int) -> int:
        """Convenience: one phone → one user_id.

        Uses SearchContacts first (server-side phone search over your
        contact graph + public directory). Falls back to
        ImportContacts if SearchContacts yields nothing.

        Raises LookupError if neither RPC resolves the phone.
        """
        q = str(phone).strip()
        if not q.startswith("+") and q.isdigit():
            q = "+" + q
        results = self.search_contacts(q)
        if results:
            return results[0].user_id
        imported = self.import_contacts([phone])
        if imported:
            return imported[0].user_id
        raise LookupError(f"no Bale user found for phone {phone!r}")

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
