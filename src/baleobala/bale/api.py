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
    ACCEPT_CALL_METHOD, AUTH_SERVICE, CallCredentials, InboundMessage,
    MEET_SERVICE, MESSAGING_SERVICE, OutPeer, PhoneToImport,
    RequestImportContacts, RequestSearchContacts, RequestSendMessage,
    RequestStartLiveKitCall, RequestStartPhoneAuth, RequestValidateCode,
    ResolvedContact, ResponseAuth, encode_accept_call,
    find_inbound_messages, parse_call_credentials,
    parse_incoming_call_offer,
    parse_import_contacts_response, parse_response_auth,
    parse_search_contacts_response, parse_transaction_hash,
    parse_update_call_received,
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
        # peer_id → callback(body: bytes). Registered via listen_messages;
        # fires on any UpdateMessage push whose peer/sender matches.
        self._message_subs: dict[int, Callable[[bytes], None]] = {}
        self._seen_rids: set[int] = set()
        self._seen_rids_order: list[int] = []
        # Tracks the callId of the last UpdateCallReceived we auto-
        # accepted so we don't fire AcceptCall twice for the same call
        # (the server often re-sends the same update several times).
        self._accepted_call_id: int | None = None

    # ------------------------------------------------------------------ lifecycle

    def bootstrap(self) -> List[Endpoint]:
        """Fetch the live MTProto endpoints list. Not used by the WS
        path but exposed so callers can verify connectivity."""
        if self._endpoints is None:
            self._endpoints = fetch_endpoints()
        return self._endpoints

    def start(self, timeout: float = 15.0, *, allow_unauth: bool = False) -> None:
        if not self._jwt and not allow_unauth:
            raise RuntimeError(
                "BaleApiClient requires a JWT access_token. Pass it to "
                "__init__ or pass allow_unauth=True if you're about to "
                "run the phone-auth flow (StartPhoneAuth / ValidateCode)."
            )
        kwargs = {}
        if self._ws_url:
            kwargs["url"] = self._ws_url
        self._ws = WsClient(jwt=self._jwt, on_update=self._dispatch_update, **kwargs)
        self._ws.start(timeout=timeout)
        if self._jwt:
            self._subscribe_updates()

    # Payload copied from the live web capture
    # captures/ws-live/00002_cl_10199508.bin: the `optimizations` field
    # (tag 2, packed repeated int32) carrying [8, 10, 12]. An empty
    # body was observed to be dropped by the server for some accounts;
    # this minimal packed-optimizations payload matches what the web
    # client sends and has reliably opened the push stream on both
    # accounts tested.
    _GET_DIFF_PAYLOAD = bytes.fromhex("120308 0a0c".replace(" ", ""))

    def _subscribe_updates(self) -> None:
        """Ask the server to start pushing updates on this WS session.

        Web client issues /bale.ghasedak.v1.GhasedakService/GetDiff right
        after the WS handshake. Without this, the server never pushes
        UpdateMessage / incoming-call credentials to us — the phone
        session gets them instead. Fire-and-forget: we don't need the
        ack, only the push stream that follows.
        """
        assert self._ws is not None
        try:
            log.info("subscribing to update stream via GetDiff (fire-and-forget)")
            self._ws.send_oneway(
                "bale.ghasedak.v1.GhasedakService",
                "GetDiff",
                self._GET_DIFF_PAYLOAD,
            )
        except Exception:  # noqa: BLE001
            log.exception("GetDiff send failed; pushes may not arrive")

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
        creds_timeout: float = 120.0,
        cancel_event=None,
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
        log.info(
            "sending StartCall peer=%d video=%s creds_timeout=%.1fs",
            peer_id,
            video,
            creds_timeout,
        )
        resp = self._ws.rpc(
            "bale.meet.v1.Meet", "StartCall", payload, timeout=10.0,
        )
        log.info("StartCall ack: seq=%s payload=%dB", resp.seq, len(resp.payload))

        # For outbound calls, Bale returns the LiveKit credentials in
        # the RPC response payload itself (verified live 2026-04-19).
        # For inbound calls, creds arrive via a push update instead.
        creds = parse_call_credentials(resp.raw)
        if creds is not None:
            log.info("StartCall returned credentials inline: room=%s", creds.room)
            self._last_creds = creds
            self._creds_event.set()
            return creds

        # Fallback: wait for a push update (inbound-call style).
        log.info("StartCall ack had no inline credentials; waiting for push update")
        import time
        deadline = time.monotonic() + creds_timeout
        while not self._creds_event.is_set():
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("call cancelled by user")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    "StartCall ACKed but no credentials in response or push "
                    f"within {creds_timeout}s"
                )
            self._creds_event.wait(timeout=min(0.25, remaining))
        assert self._last_creds is not None
        return self._last_creds

    def import_contacts(
        self, phones: list[str | int], name_prefix: str = "baleobala",
    ) -> list[ResolvedContact]:
        """Resolve phone numbers to user_ids via bale.users.v1.Users/ImportContacts.

        Accepts phones in any of these forms:
          - E.164 with '+' prefix: "+989120000000"
          - digits-only string:    "989120000000"
          - int:                   989120000000

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

    # ------------------------------------------------------------------ phone auth

    def start_phone_auth(
        self,
        *,
        phone_number: int,
        app_id: int,
        api_key: str,
        device_hash: bytes,
        device_title: str = "baleobala",
    ) -> str:
        """Send SMS OTP to `phone_number`. Returns `transaction_hash`
        to hand to validate_code(). Wire format unverified — Bale's
        app_id + api_key must be captured from a real client."""
        if self._ws is None:
            raise RuntimeError("BaleApiClient not started")
        req = RequestStartPhoneAuth(
            phone_number=phone_number,
            app_id=app_id,
            api_key=api_key,
            device_hash=device_hash,
            device_title=device_title,
        )
        resp = self._ws.rpc(
            AUTH_SERVICE, "StartPhoneAuth", req.encode(), timeout=15.0,
        )
        tx_hash = parse_transaction_hash(resp.payload or resp.raw)
        if not tx_hash:
            raise RuntimeError(
                "StartPhoneAuth response lacks transaction_hash; "
                f"raw={resp.raw[:80]!r}..."
            )
        return tx_hash

    def validate_code(self, transaction_hash: str, code: str) -> ResponseAuth:
        """Submit the SMS code. Returns ResponseAuth with the JWT on
        success. Raises on failure or when SignUp is required (new
        account — use sign_up() after this raises)."""
        if self._ws is None:
            raise RuntimeError("BaleApiClient not started")
        req = RequestValidateCode(transaction_hash=transaction_hash, code=code)
        resp = self._ws.rpc(
            AUTH_SERVICE, "ValidateCode", req.encode(), timeout=15.0,
        )
        auth = parse_response_auth(resp.payload or resp.raw)
        if auth is None:
            raise RuntimeError(
                "ValidateCode response lacks a JWT — account may not "
                "exist yet (SignUp required)."
            )
        return auth

    def send_message(self, peer_id: int, body: bytes, *,
                     peer_type: int = 1) -> None:
        """Send a text message to `peer_id` via
        `/bale.messaging.v2.Messaging/SendMessage`.

        `body` is sent as UTF-8 text; the RPC transport base64-encodes
        binary VPN frames before handing them here. If Bale's server
        applies any Unicode canonicalization that would corrupt
        base64, consider switching the RPC transport to hex.
        """
        if self._ws is None:
            raise RuntimeError("BaleApiClient not started")
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as e:
            raise ValueError("send_message body must be valid UTF-8") from e

        req = RequestSendMessage(
            peer=OutPeer(user_id=peer_id, type=peer_type),
            text=text,
        )
        payload = req.encode()
        log.info("SendMessage peer=%d bytes=%d", peer_id, len(body))
        self._ws.rpc(MESSAGING_SERVICE, "SendMessage", payload, timeout=15.0)

    def listen_messages(
        self,
        peer_id: int,
        callback: Callable[[bytes], None],
    ) -> None:
        """Subscribe to UpdateMessage pushes from `peer_id`. The
        callback fires with the message body (UTF-8 bytes) for every
        unique inbound rid."""
        self._message_subs[peer_id] = callback

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
        if creds is not None:
            log.info("received call credentials: room=%s", creds.room)
            self._deliver_creds(creds)
        else:
            call_id = parse_update_call_received(resp.raw)
            if call_id is None:
                call_id = parse_incoming_call_offer(resp.raw)
            if call_id is not None and call_id != self._accepted_call_id:
                log.info("incoming call received: callId=%d; auto-accepting", call_id)
                self._accepted_call_id = call_id
                # Cannot block the WS recv loop on an RPC. Spawn a short
                # thread that issues AcceptCall and feeds the resulting
                # credentials back into the same delivery path.
                threading.Thread(
                    target=self._accept_and_deliver,
                    args=(call_id,),
                    daemon=True,
                    name="baleobala-accept-call",
                ).start()

        if self._message_subs:
            self._dispatch_inbound_messages(resp.raw)

    def _deliver_creds(self, creds: CallCredentials) -> None:
        self._last_creds = creds
        self._creds_event.set()
        if self._on_incoming_creds is not None:
            try:
                self._on_incoming_creds(creds)
            except Exception:  # noqa: BLE001
                log.exception("on_incoming_creds callback failed")

    def _accept_and_deliver(self, call_id: int) -> None:
        if self._ws is None:
            return
        try:
            log.info("sending AcceptCall callId=%d", call_id)
            payload = encode_accept_call(call_id, invite_enable=True)
            resp = self._ws.rpc(MEET_SERVICE, ACCEPT_CALL_METHOD, payload, timeout=15.0)
            log.info("AcceptCall ack: seq=%s payload=%dB", resp.seq, len(resp.payload))
            creds = parse_call_credentials(resp.raw)
            if creds is None:
                log.error("AcceptCall response had no LiveKit credentials")
                return
            log.info("AcceptCall returned credentials: room=%s", creds.room)
            self._deliver_creds(creds)
        except Exception:  # noqa: BLE001
            log.exception("AcceptCall failed for callId=%d", call_id)

    def _dispatch_inbound_messages(self, raw: bytes) -> None:
        messages = find_inbound_messages(raw)
        for m in messages:
            if m.rid and m.rid in self._seen_rids:
                continue
            if m.rid:
                self._remember_rid(m.rid)
            cb = (
                self._message_subs.get(m.sender_uid)
                or self._message_subs.get(m.peer_user_id)
            )
            if cb is None:
                continue
            try:
                cb(m.text.encode("utf-8"))
            except Exception:  # noqa: BLE001
                log.exception("inbound-message callback failed")

    def _remember_rid(self, rid: int) -> None:
        self._seen_rids.add(rid)
        self._seen_rids_order.append(rid)
        while len(self._seen_rids_order) > 1024:
            old = self._seen_rids_order.pop(0)
            self._seen_rids.discard(old)
