from __future__ import annotations

import pytest

from userbot_bale.bale.mtproto.authkey import (
    AuthKeyNegotiationNotReady,
    AuthKeyNegotiator,
    BaleP256DhHeuristicHandshakeCodec,
    MtprotoAuthKey,
    PlaceholderHandshakeCodec,
    ServerHandshakeMaterial,
)
from userbot_bale.bale.rpc_envelope import _enc_len_delim


class _Stream:
    def __init__(self, server_reply: bytes) -> None:
        self.server_reply = server_reply
        self.sent: list[bytes] = []

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def recv(self, n: int) -> bytes:
        del n
        reply = self.server_reply
        self.server_reply = b""
        return reply


class _Conn:
    def __init__(self, server_reply: bytes) -> None:
        self.stream = _Stream(server_reply)


class _Codec:
    def build_client_hello(self, *, endpoint_pin: str) -> tuple[bytes, bytes]:
        assert endpoint_pin == "a" * 64
        return (b"nonce-1", b"hello-1")

    def parse_server_hello(
        self,
        payload: bytes,
        *,
        endpoint_pin: str,
        client_nonce: bytes,
    ) -> ServerHandshakeMaterial:
        assert payload == b"server-1"
        assert endpoint_pin == "a" * 64
        assert client_nonce == b"nonce-1"
        return ServerHandshakeMaterial(
            server_nonce=b"srv-nonce",
            payload=payload,
            fingerprint=endpoint_pin,
        )

    def build_client_dh(
        self,
        material: ServerHandshakeMaterial,
        *,
        endpoint_pin: str,
        client_nonce: bytes,
    ) -> tuple[bytes, MtprotoAuthKey]:
        assert material.server_nonce == b"srv-nonce"
        assert endpoint_pin == "a" * 64
        assert client_nonce == b"nonce-1"
        return (
            b"client-dh-1",
            MtprotoAuthKey(key_id="kid-1", key_hex="cafe"),
        )


def test_placeholder_handshake_codec_fails_at_client_hello() -> None:
    codec = PlaceholderHandshakeCodec()
    with pytest.raises(AuthKeyNegotiationNotReady):
        codec.build_client_hello(endpoint_pin="a" * 64)


def test_auth_key_negotiator_drives_handshake_with_codec() -> None:
    conn = _Conn(b"server-1")
    negotiator = AuthKeyNegotiator(codec=_Codec())
    auth_key = negotiator.negotiate(conn, endpoint_pin="a" * 64)
    assert auth_key.key_id == "kid-1"
    assert auth_key.key_hex == "cafe"
    assert auth_key.fingerprint == "a" * 64
    assert conn.stream.sent == [b"hello-1", b"client-dh-1"]
    assert negotiator.last_transcript is not None
    assert negotiator.last_transcript.server_hello == b"server-1"


def test_auth_key_negotiator_raises_on_empty_server_reply() -> None:
    conn = _Conn(b"")
    negotiator = AuthKeyNegotiator(codec=_Codec())
    with pytest.raises(ConnectionError):
        negotiator.negotiate(conn, endpoint_pin="a" * 64)


def test_bale_p256dh_heuristic_codec_roundtrip() -> None:
    client = BaleP256DhHeuristicHandshakeCodec()
    server = BaleP256DhHeuristicHandshakeCodec()

    client_nonce, client_hello = client.build_client_hello(endpoint_pin="a" * 64)
    _, server_hello = server.build_client_hello(endpoint_pin="a" * 64)
    material = client.parse_server_hello(
        _enc_len_delim(7, server_hello),
        endpoint_pin="a" * 64,
        client_nonce=client_nonce,
    )
    client_dh, client_key = client.build_client_dh(
        material,
        endpoint_pin="a" * 64,
        client_nonce=client_nonce,
    )

    server_material = server.parse_server_hello(
        client_hello,
        endpoint_pin="a" * 64,
        client_nonce=client_nonce,
    )
    _, server_key = server.build_client_dh(
        server_material,
        endpoint_pin="a" * 64,
        client_nonce=client_nonce,
    )

    assert client_dh == b""
    assert client_key.key_hex == server_key.key_hex
    assert client_key.key_id == server_key.key_id


def test_bale_p256dh_heuristic_codec_rejects_missing_public_key() -> None:
    codec = BaleP256DhHeuristicHandshakeCodec()
    with pytest.raises(ValueError):
        codec.parse_server_hello(
            b"\n\x03bad",
            endpoint_pin="a" * 64,
            client_nonce=b"nonce",
        )
