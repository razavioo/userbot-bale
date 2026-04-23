from __future__ import annotations

import pytest

from baleobala.bale.endpoints import Endpoint
from baleobala.bale.mtproto.authkey import (
    AuthKeyNegotiationNotReady,
    MtprotoAuthKey,
    PlaceholderHandshakeCodec,
)
from baleobala.bale.mtproto.store import PersistedMtprotoSession
from baleobala.bale.mtproto_backend import (
    MtprotoMessagingBackend,
    MtprotoTransportNotReady,
)


def test_mtproto_backend_start_reaches_endpoint_then_fails_closed(monkeypatch) -> None:
    calls: list[Endpoint] = []

    endpoint = Endpoint(
        scheme="tcp",
        pin="a" * 64,
        host="rpc-c002.bale.ai",
        ip="2.3.4.5",
        port=443,
        id=1014,
    )

    class _Conn:
        def close(self) -> None:
            calls.append(endpoint)

    monkeypatch.setattr(
        "baleobala.bale.mtproto_backend.fetch_endpoints",
        lambda: [endpoint],
    )
    monkeypatch.setattr(
        "baleobala.bale.mtproto_backend.connect",
        lambda ep: _Conn(),
    )

    backend = MtprotoMessagingBackend(jwt="jwt-token")
    backend._negotiator = type(backend._negotiator)(codec=PlaceholderHandshakeCodec())
    with pytest.raises(AuthKeyNegotiationNotReady):
        backend.start()
    assert calls == []
    assert backend._state is None


def test_mtproto_backend_send_receive_raise_not_ready() -> None:
    backend = MtprotoMessagingBackend(jwt="jwt-token")
    with pytest.raises(MtprotoTransportNotReady):
        backend.send_message(1, b"x")
    with pytest.raises(MtprotoTransportNotReady):
        backend.listen_messages(1, lambda body: None)


class _FakeRpcClient:
    def __init__(self) -> None:
        self.started = False
        self.closed = False
        self.subscribers = []
        self.calls = []

    def start(self) -> None:
        self.started = True

    def close(self) -> None:
        self.closed = True

    def subscribe(self, callback) -> None:  # noqa: ANN001
        self.subscribers.append(callback)

    def call(self, request) -> bytes:  # noqa: ANN001
        self.calls.append(request)
        return b"ok"

    def emit(self, payload: bytes) -> None:
        for callback in list(self.subscribers):
            callback(payload)


class _Negotiator:
    def __init__(self) -> None:
        self.calls = []

    def negotiate(self, conn, *, endpoint_pin: str) -> MtprotoAuthKey:  # noqa: ANN001
        self.calls.append((conn, endpoint_pin))
        return MtprotoAuthKey(key_id="kid-live", key_hex="cafe", fingerprint=endpoint_pin)


class _ConnStream:
    def __init__(self) -> None:
        self.closed = False

    def recv(self, n: int) -> bytes:
        del n
        return b""

    def sendall(self, data: bytes) -> None:
        del data

    def close(self) -> None:
        self.closed = True


class _EndpointConn:
    def __init__(self, endpoint: Endpoint) -> None:
        self.endpoint = endpoint
        self.stream = _ConnStream()

    def close(self) -> None:
        self.stream.close()


def _build_fake_update_message(*, peer_id: int, sender_uid: int, rid: int, text: str) -> bytes:
    from baleobala.bale.protos import OutPeer, _encode_message_with_text
    from baleobala.bale.rpc_envelope import _enc_len_delim, _enc_tag, _enc_varint

    peer_bytes = OutPeer(user_id=peer_id, type=1).encode()
    msg_bytes = _encode_message_with_text(text)
    body = bytearray()
    body += _enc_len_delim(1, peer_bytes)
    body += _enc_tag(2, 0) + _enc_varint(sender_uid)
    body += _enc_tag(4, 0) + _enc_varint(rid)
    body += _enc_len_delim(5, msg_bytes)
    return bytes(body)


def test_mtproto_backend_uses_rpc_client_for_send_and_receive(monkeypatch) -> None:
    import tempfile

    monkeypatch.setenv("BALEOBALA_HOME", tempfile.mkdtemp(prefix="baleobala-mtproto-test-"))
    rpc = _FakeRpcClient()
    backend = MtprotoMessagingBackend(jwt="jwt-token", rpc_client=rpc)
    backend.start()
    seen: list[bytes] = []
    backend.listen_messages(42, seen.append)
    backend.send_message(42, b"hello")
    assert rpc.started is True
    assert len(rpc.calls) == 1
    rpc.emit(_build_fake_update_message(peer_id=42, sender_uid=42, rid=7, text="hello"))
    assert seen == [b"hello"]
    backend.stop()
    assert rpc.closed is True


def test_mtproto_backend_live_start_negotiates_auth_key(monkeypatch) -> None:
    import tempfile

    monkeypatch.setenv("BALEOBALA_HOME", tempfile.mkdtemp(prefix="baleobala-mtproto-live-"))
    endpoint = Endpoint(
        scheme="tls",
        pin="a" * 64,
        host="rpc-c002.bale.ai",
        ip="1.2.3.4",
        port=443,
        id=1013,
    )
    negotiator = _Negotiator()
    monkeypatch.setattr(
        "baleobala.bale.mtproto_backend.fetch_endpoints",
        lambda: [endpoint],
    )
    backend = MtprotoMessagingBackend(
        jwt="jwt-token",
        negotiator=negotiator,
        connector=lambda ep: _EndpointConn(ep),
    )
    backend.start()
    assert backend._state is not None
    assert backend._state.endpoint == endpoint
    assert backend._state.auth_key.key_id == "kid-live"
    assert negotiator.calls and negotiator.calls[0][1] == endpoint.pin
    backend.stop()


def test_mtproto_backend_loads_persisted_session(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    rpc = _FakeRpcClient()
    backend = MtprotoMessagingBackend(jwt="jwt-token", rpc_client=rpc)
    backend._store.save(  # type: ignore[attr-defined]
        PersistedMtprotoSession(
            endpoint_host="rpc-c002.bale.ai",
            endpoint_port=443,
            endpoint_scheme="tls",
            endpoint_pin="a" * 64,
            auth_key_id="kid-1",
            auth_key_hex="beef",
            session_id="sess-1",
            created_at=1.0,
            updated_at=2.0,
        )
    )
    backend.start()
    assert backend._state is not None
    assert backend._state.session_id == "sess-1"
    assert backend._state.auth_key.key_hex == "beef"
    backend.stop()
