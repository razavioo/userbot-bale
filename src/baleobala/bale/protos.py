"""
Hand-written protobuf encoders/decoders for the Bale messages we need.

These are narrow, one-off implementations that match the on-the-wire
bytes observed in captures/ws-live/. We use them in place of a full
.proto compilation because only a handful of messages are needed and
the Java decompile's obfuscation makes clean .proto generation fiddly.

Messages implemented:
    OutPeer           — peer identifier (type + user/chat id)
    BooleanValue      — wrapped boolean (for optional bools)
    RequestStartLiveKitCall — the call-init request body
    ResponseCall      — minimal decoder (just what the call path needs)

Field numbers were read from `ai.bale.proto.MeetOuterClass*` in the
APK decompile and verified byte-for-byte against live WS captures on
2026-04-19.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from baleobala.bale.rpc_envelope import (
    _dec_tag, _dec_varint, _enc_len_delim, _enc_tag, _enc_varint,
)


PEER_TYPE_PRIVATE = 1
PEER_TYPE_GROUP = 2
PEER_TYPE_CHANNEL = 3


@dataclass(frozen=True)
class OutPeer:
    """ai.bale.proto.PeersStruct.OutPeer

    Wire: field 1 = peer type (varint), field 2 = id (varint).
    Access-hash field exists but is omitted on the wire for private
    peers that are the user's own contacts (no hash needed).
    """
    user_id: int
    type: int = PEER_TYPE_PRIVATE

    def encode(self) -> bytes:
        out = bytearray()
        out += _enc_tag(1, 0) + _enc_varint(self.type)
        out += _enc_tag(2, 0) + _enc_varint(self.user_id)
        return bytes(out)


def _enc_bool_value(value: bool) -> bytes:
    """CollectionsStruct.BooleanValue — { field 1 = bool }."""
    return _enc_tag(1, 0) + _enc_varint(1 if value else 0)


@dataclass(frozen=True)
class RequestStartLiveKitCall:
    """ai.bale.proto.MeetOuterClass.RequestStartLiveKitCall

    Wire observed (inside an outer wrapper at tag 6):
        field 1 (len-delim) = OutPeer
        field 2 (varint)    = rid  (random int64 call identifier)
        field 3 (varint)    = video (bool; 0 for audio-only)
        field 4 (len-delim) = BooleanValue (inviteEnable wrapper)
    """
    peer: OutPeer
    rid: int | None = None   # auto-generated when None
    video: bool = False
    invite_enable: bool = True

    def encode(self) -> bytes:
        """Encode the inner RequestStartLiveKitCall payload."""
        rid = self.rid if self.rid is not None else secrets.randbits(55)
        body = bytearray()
        body += _enc_len_delim(1, self.peer.encode())
        body += _enc_tag(2, 0) + _enc_varint(rid)
        if self.video:
            body += _enc_tag(3, 0) + _enc_varint(1)
        if self.invite_enable:
            body += _enc_len_delim(4, _enc_bool_value(True))
        return bytes(body)

    def encode_as_rpc_payload(self) -> bytes:
        """Wrap in the outer RPC-payload envelope (field 6).

        The web client wraps the Request message inside an envelope at
        tag 6 of the bale.meet.v1.Meet/StartCall RPC payload. This is
        the exact layout the server accepts.
        """
        return _enc_len_delim(6, self.encode())


@dataclass
class CallCredentials:
    """What comes back from StartCall / what we'd get on a push update."""
    url: str
    token: str
    room: str
    raw: bytes = b""


def parse_call_credentials(buf: bytes) -> CallCredentials | None:
    """Extract LiveKit url + JWT + room out of an opaque server update.

    The message layout is nested protobuf (sub-messages at tags we
    haven't all mapped), but the url + token + room uuid appear as
    length-prefixed strings at predictable positions. We scan byte-wise.
    """
    import re
    url_m = re.search(rb'(wss://[a-zA-Z0-9./\-]+\.(?:ir|ai))', buf)
    tok_m = re.search(rb'(eyJhbGciOi[A-Za-z0-9_\-.]{100,})', buf)
    room_m = re.search(
        rb'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', buf,
    )
    if not (url_m and tok_m):
        return None
    return CallCredentials(
        url=url_m.group(0).decode("ascii"),
        token=tok_m.group(0).decode("ascii"),
        room=room_m.group(0).decode("ascii") if room_m else "",
        raw=buf,
    )
