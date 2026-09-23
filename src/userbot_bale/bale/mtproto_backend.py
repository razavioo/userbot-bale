"""MTProto-backed Bale messaging backend."""

from __future__ import annotations

from userbot_bale.bale.endpoints import Endpoint, fetch_endpoints
from userbot_bale.bale.messaging_backend import MessageCallback
from userbot_bale.bale.mtproto import MtpRpcClient
from userbot_bale.bale.mtproto.authkey import AuthKeyNegotiator, MtprotoAuthKey
from userbot_bale.bale.mtproto.endpoint import connect
from userbot_bale.bale.mtproto.session import MtprotoSession, MtprotoSessionState
from userbot_bale.bale.mtproto.store import MtprotoSessionStore
from userbot_bale.bale.protos import (
    MESSAGING_SERVICE,
    OutPeer,
    RequestSendMessage,
    find_inbound_messages,
)
from userbot_bale.bale.rpc_envelope import Request


class MtprotoTransportNotReady(RuntimeError):
    """Raised when the Bale-native messaging path is selected before the
    capture-gated MTProto session/RPC stack has been implemented."""


class MtprotoMessagingBackend:
    """Bale messaging backend intended for VPN fallback transport.

    When no RPC client is injected, this backend opens a Bale endpoint,
    performs auth-key negotiation (default codec: heuristic P-256 ECDH),
    and starts the MTProto RPC session. The handshake codec is still
    heuristic, so this path should be treated as experimental until
    validated against production captures (docs/CAPTURE.md). With an
    injected RPC client (tests / pre-established session), send/listen
    work immediately.
    """

    def __init__(
        self,
        jwt: str | None = None,
        *,
        rpc_client=None,  # type: ignore[no-untyped-def]
        negotiator: AuthKeyNegotiator | None = None,
        connector=None,  # type: ignore[no-untyped-def]
    ) -> None:
        self._jwt = jwt
        self._rpc = rpc_client
        self._session: MtprotoSession | None = None
        self._state: MtprotoSessionState | None = None
        self._message_subs: dict[int, MessageCallback] = {}
        self._seen_rids: set[int] = set()
        self._seen_rids_order: list[int] = []
        self._store = MtprotoSessionStore()
        self._negotiator = negotiator or AuthKeyNegotiator()
        self._connector = connector or connect

    def bootstrap(self) -> list[Endpoint]:
        return fetch_endpoints()

    def start(self, timeout: float = 15.0) -> None:
        persisted = self._store.load()
        if persisted is not None:
            self._state = MtprotoSessionState(
                endpoint=Endpoint(
                    scheme=persisted.endpoint_scheme,
                    pin=persisted.endpoint_pin,
                    host=persisted.endpoint_host,
                    ip="",
                    port=persisted.endpoint_port,
                    id=0,
                ),
                auth_key=persisted.auth_key(),
                session_id=persisted.session_id,
                created_at=persisted.created_at,
                updated_at=persisted.updated_at,
            )
        if self._rpc is not None:
            if self._state is None:
                self._state = MtprotoSessionState.create(
                    endpoint=Endpoint(
                        scheme="tls",
                        pin="",
                        host="mtproto-rpc",
                        ip="127.0.0.1",
                        port=0,
                        id=0,
                    ),
                    auth_key=MtprotoAuthKey(key_id="", key_hex=""),
                )
            self._session = MtprotoSession(
                state=self._state,
                rpc_client=self._rpc,
                store=self._store,
            )
            self._session.start()
            self._rpc.subscribe(self._dispatch_update)
            return
        del timeout, self._jwt
        endpoints = self.bootstrap()
        if not endpoints:
            raise RuntimeError("no Bale MTProto endpoints available")
        endpoint = endpoints[0]
        conn = self._connector(endpoint)
        auth_key = self._negotiator.negotiate(conn, endpoint_pin=endpoint.pin)
        self._rpc = MtpRpcClient(conn)
        self._state = MtprotoSessionState.create(
            endpoint=endpoint,
            auth_key=auth_key,
        )
        self._session = MtprotoSession(
            state=self._state,
            rpc_client=self._rpc,
            store=self._store,
        )
        self._session.start()
        self._rpc.subscribe(self._dispatch_update)

    def stop(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None
        elif self._rpc is not None:
            close = getattr(self._rpc, "close", None)
            if callable(close):
                close()
        self._state = None

    def send_message(self, peer_id: int, body: bytes, *, peer_type: int = 1) -> None:
        if self._rpc is None:
            raise MtprotoTransportNotReady(
                "MTProto Bale messaging send path is not implemented yet."
            )
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as e:
            raise ValueError("send_message body must be valid UTF-8") from e
        req = RequestSendMessage(
            peer=OutPeer(user_id=peer_id, type=peer_type),
            text=text,
        )
        rpc_req = Request(service=MESSAGING_SERVICE, method="SendMessage", payload=req.encode())
        self._rpc.call(rpc_req)

    def listen_messages(self, peer_id: int, callback: MessageCallback) -> None:
        if self._rpc is None:
            raise MtprotoTransportNotReady(
                "MTProto Bale messaging receive path is not implemented yet."
            )
        self._message_subs[peer_id] = callback

    def _dispatch_update(self, raw: bytes) -> None:
        messages = find_inbound_messages(raw)
        for message in messages:
            if message.rid and message.rid in self._seen_rids:
                continue
            if message.rid:
                self._remember_rid(message.rid)
            cb = (
                self._message_subs.get(message.sender_uid)
                or self._message_subs.get(message.peer_user_id)
            )
            if cb is None:
                continue
            cb(message.text.encode("utf-8"))

    def _remember_rid(self, rid: int) -> None:
        self._seen_rids.add(rid)
        self._seen_rids_order.append(rid)
        while len(self._seen_rids_order) > 1024:
            old = self._seen_rids_order.pop(0)
            self._seen_rids.discard(old)
