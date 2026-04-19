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


# ============================================================
# Contact resolution (phone → user_id)
# ============================================================

@dataclass(frozen=True)
class PhoneToImport:
    """ai.bale.proto.UsersStruct.PhoneToImport

    Wire:
        field 1 (varint) = phone_number (int64, digits only, no '+')
        field 2 (string) = name (free-form label; server returns users
                           matched on phone regardless of name)
    """
    phone_number: int
    name: str = ""

    def encode(self) -> bytes:
        out = bytearray()
        out += _enc_tag(1, 0) + _enc_varint(self.phone_number)
        if self.name:
            out += _enc_len_delim(2, self.name.encode("utf-8"))
        return bytes(out)


@dataclass(frozen=True)
class RequestImportContacts:
    """ai.bale.proto.UsersOuterClass.RequestImportContacts

    Wire:
        field 1 (repeated PhoneToImport) = phones
        field 3 (repeated int)           = optimizations (safe to omit)
    """
    phones: list

    def encode(self) -> bytes:
        """Encode at the RPC-payload tag observed for ImportContacts.

        The web client wraps each RPC payload inside a per-method tag
        in the outer envelope. Empirically:
            bale.meet.v1.Meet/StartCall     → tag 6
            bale.users.v1.Users/ImportContacts → tag 8

        (Tags not sent verbatim by the web client during our capture
        were probed live against the server; tag 8 returned
        `user_rate_limited`, confirming it dispatched to the right
        handler.)
        """
        inner = bytearray()
        for p in self.phones:
            inner += _enc_len_delim(1, p.encode())
        return _enc_len_delim(8, bytes(inner))


@dataclass(frozen=True)
class ResolvedContact:
    phone_number: int
    user_id: int
    access_hash: int
    name: str = ""


@dataclass(frozen=True)
class RequestSearchContacts:
    """bale.users.v1.Users/SearchContacts

    Server-side search over the user's contacts + public directory.
    Takes a free-form query — phone, name, or username — and returns
    matching UserOutPeers.

    Wire observed (via live probing 2026-04-19):
        outer tag 4 (len-delim) {
            field 1 (string) = query
        }
    """
    query: str

    def encode(self) -> bytes:
        inner = _enc_len_delim(1, self.query.encode("utf-8"))
        return _enc_len_delim(4, inner)


def parse_search_contacts_response(buf: bytes) -> list:
    """Parse ResponseSearchContacts → list[ResolvedContact].

    Layout: repeated UserOutPeer at tag 2 (a top-level list).
    """
    out: list[ResolvedContact] = []
    pos = 0
    while pos < len(buf):
        fn, wt, pos = _dec_tag(buf, pos)
        if wt == 2:
            ln, pos = _dec_varint(buf, pos)
            inner = buf[pos:pos + ln]
            pos += ln
            if fn == 2:
                user_id = access_hash = 0
                p = 0
                while p < len(inner):
                    f, w, p = _dec_tag(inner, p)
                    if w == 0:
                        v, p = _dec_varint(inner, p)
                        if f == 1:
                            user_id = v
                        elif f == 2:
                            access_hash = v
                    elif w == 2:
                        l, p = _dec_varint(inner, p)
                        p += l
                if user_id:
                    out.append(ResolvedContact(
                        phone_number=0, user_id=user_id,
                        access_hash=access_hash,
                    ))
        elif wt == 0:
            _, pos = _dec_varint(buf, pos)
    return out


def parse_import_contacts_response(buf: bytes) -> list:
    """Parse the ResponseImportContacts payload → ResolvedContact list.

    The response carries:
        field 1 (repeated User)          — user records with name + etc
        field 4 (repeated UserOutPeer)   — user_id + access_hash pairs

    We pair them by order (same length expected) and attach the phone
    from the requesting message. A light-touch parser — we only need
    user_id out of the response; if the protos ever expand, add fields.
    """
    users_blocks: list[bytes] = []
    user_peers_blocks: list[bytes] = []

    pos = 0
    # Unwrap any outer framing the server adds (observed tag 1 wraps
    # the whole payload, same as StartCall response).
    while pos < len(buf):
        fnum, wt, pos = _dec_tag(buf, pos)
        if wt == 2:
            length, pos = _dec_varint(buf, pos)
            inner = buf[pos:pos + length]
            pos += length
            if fnum == 1:
                users_blocks.append(inner)
            elif fnum == 4:
                user_peers_blocks.append(inner)
            elif fnum in (2, 3):
                # keep digging — server sometimes nests the real
                # ResponseImportContacts inside another wrapper
                sub = parse_import_contacts_response(inner)
                if sub:
                    return sub
        elif wt == 0:
            _, pos = _dec_varint(buf, pos)
        else:
            break

    out: list[ResolvedContact] = []
    for peer_blob in user_peers_blocks:
        # UserOutPeer: field 1 = user_id, field 2 = access_hash
        user_id = access_hash = 0
        p = 0
        while p < len(peer_blob):
            f, w, p = _dec_tag(peer_blob, p)
            if w == 0:
                v, p = _dec_varint(peer_blob, p)
                if f == 1:
                    user_id = v
                elif f == 2:
                    access_hash = v
            elif w == 2:
                l, p = _dec_varint(peer_blob, p)
                p += l
        if user_id:
            out.append(ResolvedContact(
                phone_number=0, user_id=user_id, access_hash=access_hash,
            ))
    return out


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
