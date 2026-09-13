"""Auth-key models and handshake boundary for Bale MTProto."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from userbot_bale.bale.rpc_envelope import _dec_tag, _dec_varint, _enc_len_delim


class AuthKeyNegotiationNotReady(RuntimeError):
    """Raised when live MTProto auth-key negotiation is requested before
    we have the capture-validated Bale handshake transcript."""


@dataclass(frozen=True)
class MtprotoAuthKey:
    key_id: str
    key_hex: str
    fingerprint: str = ""
    created_at: float = 0.0

    @classmethod
    def from_bytes(
        cls,
        key: bytes,
        *,
        fingerprint: str = "",
        created_at: float = 0.0,
    ) -> "MtprotoAuthKey":
        import hashlib

        key_id = hashlib.sha1(key).digest()[:8].hex()
        return cls(
            key_id=key_id,
            key_hex=key.hex(),
            fingerprint=fingerprint,
            created_at=created_at,
        )

    def key_bytes(self) -> bytes:
        return bytes.fromhex(self.key_hex)


@dataclass(frozen=True)
class HandshakeTranscript:
    endpoint_pin: str
    client_nonce: bytes
    client_hello: bytes
    server_hello: bytes
    client_dh: bytes
    auth_key: MtprotoAuthKey


@dataclass(frozen=True)
class ServerHandshakeMaterial:
    server_nonce: bytes
    payload: bytes
    fingerprint: str = ""


class HandshakeCodec(Protocol):
    def build_client_hello(self, *, endpoint_pin: str) -> tuple[bytes, bytes]: ...

    def parse_server_hello(
        self,
        payload: bytes,
        *,
        endpoint_pin: str,
        client_nonce: bytes,
    ) -> ServerHandshakeMaterial: ...

    def build_client_dh(
        self,
        material: ServerHandshakeMaterial,
        *,
        endpoint_pin: str,
        client_nonce: bytes,
    ) -> tuple[bytes, MtprotoAuthKey]: ...


class PlaceholderHandshakeCodec:
    """Default codec that marks the exact live blocker.

    The negotiator itself is real and testable. This codec is the only
    piece still waiting on capture-validated Bale wire bytes.
    """

    def build_client_hello(self, *, endpoint_pin: str) -> tuple[bytes, bytes]:
        del endpoint_pin
        raise AuthKeyNegotiationNotReady(
            "Bale MTProto client-hello bytes are still capture-gated."
        )

    def parse_server_hello(
        self,
        payload: bytes,
        *,
        endpoint_pin: str,
        client_nonce: bytes,
    ) -> ServerHandshakeMaterial:
        del payload, endpoint_pin, client_nonce
        raise AuthKeyNegotiationNotReady(
            "Bale MTProto server-hello parsing is still capture-gated."
        )

    def build_client_dh(
        self,
        material: ServerHandshakeMaterial,
        *,
        endpoint_pin: str,
        client_nonce: bytes,
    ) -> tuple[bytes, MtprotoAuthKey]:
        del material, endpoint_pin, client_nonce
        raise AuthKeyNegotiationNotReady(
            "Bale MTProto DH completion bytes are still capture-gated."
        )


def _walk_len_delim(buf: bytes):
    pos = 0
    n = len(buf)
    while pos < n:
        try:
            fn, wt, pos = _dec_tag(buf, pos)
        except (IndexError, ValueError):
            return
        if wt == 0:
            try:
                _, pos = _dec_varint(buf, pos)
            except (IndexError, ValueError):
                return
        elif wt == 2:
            try:
                ln, pos = _dec_varint(buf, pos)
            except (IndexError, ValueError):
                return
            chunk = buf[pos:pos + ln]
            pos += ln
            yield fn, chunk
        elif wt == 1:
            pos += 8
        elif wt == 5:
            pos += 4
        else:
            return


def _is_ec_point(buf: bytes) -> bool:
    return len(buf) == 65 and buf[:1] == b"\x04"


def _find_ec_point(buf: bytes, *, field_number: int) -> bytes | None:
    for fn, chunk in _walk_len_delim(buf):
        if fn == field_number and _is_ec_point(chunk):
            return chunk
        found = _find_ec_point(chunk, field_number=field_number)
        if found is not None:
            return found
    return buf if _is_ec_point(buf) else None


def _find_server_nonce(buf: bytes, *, exclude: bytes) -> bytes:
    for _, chunk in _walk_len_delim(buf):
        if chunk != exclude and 16 <= len(chunk) <= 64:
            return chunk
        inner = _find_server_nonce(chunk, exclude=exclude) if chunk else b""
        if inner:
            return inner
    return b""


class BaleP256DhHeuristicHandshakeCodec:
    """Best-effort P-256 ECDH codec derived from Bale dex symbols.

    Ground truth we do have:
    - `P256DH_FIELD_NUMBER` appears in the APK strings.
    - `Ws.HandshakeRequest` / `Ws.HandshakeResponse` proto types exist.
    - `cryptography` is already available in this environment.

    What is still heuristic:
    - exact field numbers beyond assuming `p256_dh` uses field 1
    - whether the server sends extra nonce/proof fields
    - whether Bale expects a third client confirmation frame
    - final auth-key derivation details

    The implementation therefore does the strongest safe thing we can:
    real ECDH over P-256, recursive protobuf extraction of the server
    public key, optional nonce harvesting, and HKDF-SHA256 derivation of
    a stable auth key. The final client-DH message is empty because we
    do not yet have evidence Bale expects an extra confirmation frame.
    """

    def __init__(
        self,
        *,
        p256_dh_field_number: int = 1,
        nonce_size: int = 16,
    ) -> None:
        self._p256_dh_field_number = p256_dh_field_number
        self._nonce_size = nonce_size
        self._private_key = None

    def build_client_hello(self, *, endpoint_pin: str) -> tuple[bytes, bytes]:
        del endpoint_pin
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        self._private_key = ec.generate_private_key(ec.SECP256R1())
        public_key = self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint,
        )
        client_nonce = public_key[1 : 1 + self._nonce_size]
        return client_nonce, _enc_len_delim(self._p256_dh_field_number, public_key)

    def parse_server_hello(
        self,
        payload: bytes,
        *,
        endpoint_pin: str,
        client_nonce: bytes,
    ) -> ServerHandshakeMaterial:
        del endpoint_pin, client_nonce
        public_key = _find_ec_point(payload, field_number=self._p256_dh_field_number)
        if public_key is None:
            raise ValueError("server handshake did not contain a recognizable P-256 public key")
        server_nonce = _find_server_nonce(payload, exclude=public_key)
        return ServerHandshakeMaterial(
            server_nonce=server_nonce,
            payload=public_key,
            fingerprint="",
        )

    def build_client_dh(
        self,
        material: ServerHandshakeMaterial,
        *,
        endpoint_pin: str,
        client_nonce: bytes,
    ) -> tuple[bytes, MtprotoAuthKey]:
        if self._private_key is None:
            raise RuntimeError("client hello must be built before completing handshake")
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF

        server_public = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(),
            material.payload,
        )
        shared_secret = self._private_key.exchange(ec.ECDH(), server_public)
        salt = client_nonce + material.server_nonce
        info = b"bale-p256dh-auth-key:" + endpoint_pin.encode("ascii")
        auth_key_bytes = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt or None,
            info=info,
        ).derive(shared_secret)
        return (
            b"",
            MtprotoAuthKey.from_bytes(
                auth_key_bytes,
                fingerprint=endpoint_pin,
                created_at=time.time(),
            ),
        )


class AuthKeyNegotiator:
    """Drives the auth-key handshake around a pluggable wire codec."""

    def __init__(self, *, codec: HandshakeCodec | None = None) -> None:
        self._codec = codec or BaleP256DhHeuristicHandshakeCodec()
        self.last_transcript: HandshakeTranscript | None = None

    def negotiate(self, conn, *, endpoint_pin: str) -> MtprotoAuthKey:  # type: ignore[no-untyped-def]
        client_nonce, client_hello = self._codec.build_client_hello(endpoint_pin=endpoint_pin)
        conn.stream.sendall(client_hello)
        server_hello = conn.stream.recv(1 << 20)
        if not server_hello:
            raise ConnectionError("connection closed during MTProto handshake")
        material = self._codec.parse_server_hello(
            server_hello,
            endpoint_pin=endpoint_pin,
            client_nonce=client_nonce,
        )
        client_dh, auth_key = self._codec.build_client_dh(
            material,
            endpoint_pin=endpoint_pin,
            client_nonce=client_nonce,
        )
        conn.stream.sendall(client_dh)
        stamped = MtprotoAuthKey(
            key_id=auth_key.key_id,
            key_hex=auth_key.key_hex,
            fingerprint=auth_key.fingerprint or material.fingerprint or endpoint_pin,
            created_at=auth_key.created_at or time.time(),
        )
        self.last_transcript = HandshakeTranscript(
            endpoint_pin=endpoint_pin,
            client_nonce=client_nonce,
            client_hello=client_hello,
            server_hello=server_hello,
            client_dh=client_dh,
            auth_key=stamped,
        )
        return stamped
