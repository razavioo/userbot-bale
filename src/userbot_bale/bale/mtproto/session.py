"""MTProto session codecs, state, and message classification helpers."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from userbot_bale.bale.endpoints import Endpoint
from userbot_bale.bale.mtproto.authkey import MtprotoAuthKey
from userbot_bale.bale.mtproto.framing import Frame


@dataclass(frozen=True)
class InboundEnvelope:
    kind: str
    seq: int
    body: bytes
    message_type: int


class SessionCodec(Protocol):
    def encode_request(self, seq: int, body: bytes) -> Frame: ...

    def classify(self, frame: Frame) -> InboundEnvelope: ...


class RpcClientLike(Protocol):
    def start(self) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class PlainSessionCodec:
    """Simple pre-auth/plain-text classifier.

    This intentionally does not claim to be Bale's final authenticated
    session format. It gives the RPC layer a real transport contract now,
    while keeping the crypto handshake and exact Bale message-type values
    isolated to a later validation step.
    """

    request_type: int = 1
    response_type: int = 2
    update_type: int = 3

    def encode_request(self, seq: int, body: bytes) -> Frame:
        return Frame(seq=seq, message_type=self.request_type, body=body)

    def classify(self, frame: Frame) -> InboundEnvelope:
        if frame.message_type == self.response_type:
            return InboundEnvelope(
                kind="response",
                seq=frame.seq,
                body=frame.body,
                message_type=frame.message_type,
            )
        if frame.message_type == self.update_type:
            return InboundEnvelope(
                kind="update",
                seq=frame.seq,
                body=frame.body,
                message_type=frame.message_type,
            )
        return InboundEnvelope(
            kind="unknown",
            seq=frame.seq,
            body=frame.body,
            message_type=frame.message_type,
        )


@dataclass(frozen=True)
class MtprotoSessionState:
    endpoint: Endpoint
    auth_key: MtprotoAuthKey
    session_id: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    @classmethod
    def create(
        cls,
        *,
        endpoint: Endpoint,
        auth_key: MtprotoAuthKey,
        session_id: str = "",
    ) -> "MtprotoSessionState":
        now = time.time()
        return cls(
            endpoint=endpoint,
            auth_key=auth_key,
            session_id=session_id,
            created_at=now,
            updated_at=now,
        )


class MtprotoSession:
    """Owns a connected Bale MTProto RPC stream plus durable state."""

    def __init__(
        self,
        *,
        state: MtprotoSessionState,
        rpc_client: RpcClientLike,
        store=None,  # type: ignore[no-untyped-def]
    ) -> None:
        self.state = state
        self.rpc = rpc_client
        self._store = store

    def start(self) -> None:
        self.rpc.start()
        self._persist()

    def close(self) -> None:
        self.rpc.close()

    def _persist(self) -> None:
        if self._store is None:
            return
        from userbot_bale.bale.mtproto.store import PersistedMtprotoSession

        self._store.save(
            PersistedMtprotoSession(
                endpoint_host=self.state.endpoint.host,
                endpoint_port=self.state.endpoint.port,
                endpoint_scheme=self.state.endpoint.scheme,
                endpoint_pin=self.state.endpoint.pin,
                auth_key_id=self.state.auth_key.key_id,
                auth_key_hex=self.state.auth_key.key_hex,
                session_id=self.state.session_id,
                created_at=self.state.created_at,
                updated_at=time.time(),
            )
        )
