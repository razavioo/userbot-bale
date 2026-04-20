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
        """SearchContacts takes the query directly at top-level field 1
        (the proto's `request` string field). No outer wrap: unlike
        StartCall/ImportContacts, this RPC payload is the unwrapped
        RequestSearchContacts message body."""
        return _enc_len_delim(1, self.query.encode("utf-8"))


def parse_search_contacts_response(buf: bytes) -> list:
    """Parse ResponseSearchContacts → list[ResolvedContact].

    Per the proto:
        field 1 = repeated User          (full records, skip here)
        field 2 = repeated UserOutPeer   (user_id + access_hash — what we need)
        field 4 = repeated Group         (group results)
        field 5 = repeated GroupOutPeer

    Empty payload (6 bytes: just the envelope + empty) means no match.
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


# ============================================================
# Messaging (text send/receive for RPC transport)
# ============================================================
# Field numbers read from the APK decompile at
#   re/jadx-out/sources/ai/bale/proto/MessagingOuterClass$RequestSendMessage.java
#   re/jadx-out/sources/ai/bale/proto/MessagingStruct$Message.java
#   re/jadx-out/sources/ai/bale/proto/MessagingStruct$TextMessage.java
#   re/jadx-out/sources/ai/bale/proto/MessagingOuterClass$UpdateMessage.java
# Service/method from ir/nasim/HR5.java:
#   /bale.messaging.v2.Messaging/SendMessage
# Live-verified 2026-04-20 against a mitmproxy capture of web.bale.ai
# sending a text message. The raw bytes of the outbound RPC show:
#   Request.payload = { peer(1) | rid(2) | message(3) | ex_peer(6) }
#   message         = { text_message(15) = { text(1) | mentions(2,empty) } }
# No outer tag-6 wrapper (unlike StartCall). The web client sets ex_peer
# to the same peer as field 1; the server appears to also accept the
# request without it (unit tests exercise both). See
# tests/fixtures/sendmessage_frames.json for the exact captured bytes.

MESSAGING_SERVICE = "bale.messaging.v2.Messaging"
_TEXT_MESSAGE_FIELD = 15  # Message.text_message

def _encode_text_message(text: str) -> bytes:
    """MessagingStruct.TextMessage { field 1 = text }."""
    return _enc_len_delim(1, text.encode("utf-8"))


def _encode_message_with_text(text: str) -> bytes:
    """MessagingStruct.Message containing only TextMessage at tag 15."""
    return _enc_len_delim(_TEXT_MESSAGE_FIELD, _encode_text_message(text))


@dataclass(frozen=True)
class RequestSendMessage:
    """ai.bale.proto.MessagingOuterClass.RequestSendMessage

    Wire (from decompile):
        field 1 (len-delim) = peer (OutPeer)
        field 2 (varint)    = rid (int64)
        field 3 (len-delim) = message (Message with text_message at tag 15)
    Optional fields (is_only_for_user=4, quoted=5, ex_peer=6, is_silent=7,
    thread_id=8) are omitted — text-only send to a single peer.
    """
    peer: OutPeer
    text: str
    rid: int | None = None
    # When True, also emit ex_peer at tag 6 = same peer as tag 1, which
    # matches what web.bale.ai sends on the wire (2026-04-20). The
    # server accepts the message without ex_peer too; keep this on for
    # maximum parity.
    include_ex_peer: bool = True

    def encode(self) -> bytes:
        rid = self.rid if self.rid is not None else secrets.randbits(55)
        out = bytearray()
        out += _enc_len_delim(1, self.peer.encode())
        out += _enc_tag(2, 0) + _enc_varint(rid)
        out += _enc_len_delim(3, _encode_message_with_text(self.text))
        if self.include_ex_peer:
            out += _enc_len_delim(6, self.peer.encode())
        return bytes(out)


@dataclass(frozen=True)
class InboundMessage:
    peer_user_id: int
    sender_uid: int
    rid: int
    text: str


def _walk_len_delim(buf: bytes):
    """Iterate (field_number, bytes_or_int, wire_type) across `buf`.
    Skips malformed tails rather than raising."""
    pos = 0
    n = len(buf)
    while pos < n:
        try:
            fn, wt, pos = _dec_tag(buf, pos)
            if wt == 0:
                v, pos = _dec_varint(buf, pos)
                yield fn, v, wt
            elif wt == 2:
                ln, pos = _dec_varint(buf, pos)
                chunk = buf[pos:pos + ln]
                pos += ln
                yield fn, chunk, wt
            elif wt == 5:
                pos += 4
            elif wt == 1:
                pos += 8
            else:
                return
        except (IndexError, ValueError):
            return


def _parse_out_peer(buf: bytes) -> tuple[int, int]:
    """Returns (peer_type, user_id). Zeros if fields absent."""
    peer_type = user_id = 0
    for fn, val, wt in _walk_len_delim(buf):
        if wt == 0:
            if fn == 1:
                peer_type = val
            elif fn == 2:
                user_id = val
    return peer_type, user_id


def _parse_text_from_message(buf: bytes) -> str | None:
    """Message → text, by descending into TextMessage at tag 15.
    Returns None if no text_message present."""
    for fn, val, wt in _walk_len_delim(buf):
        if wt == 2 and fn == _TEXT_MESSAGE_FIELD:
            # TextMessage: text at field 1 (string/bytes)
            for ifn, ival, iwt in _walk_len_delim(val):
                if iwt == 2 and ifn == 1:
                    try:
                        return ival.decode("utf-8")
                    except UnicodeDecodeError:
                        return None
    return None


def _parse_update_message(buf: bytes) -> InboundMessage | None:
    """Parse one UpdateMessage body.
    Fields: peer=1, sender_uid=2, date=3, rid=4, message=5."""
    peer_user_id = 0
    sender_uid = 0
    rid = 0
    text: str | None = None
    for fn, val, wt in _walk_len_delim(buf):
        if wt == 2 and fn == 1:
            _, peer_user_id = _parse_out_peer(val)
        elif wt == 0 and fn == 2:
            sender_uid = val
        elif wt == 0 and fn == 4:
            rid = val
        elif wt == 2 and fn == 5:
            text = _parse_text_from_message(val)
    if text is None:
        return None
    return InboundMessage(
        peer_user_id=peer_user_id,
        sender_uid=sender_uid,
        rid=rid,
        text=text,
    )


# ============================================================
# Phone auth (Phase 12 — /bale.auth.v1.Auth/StartPhoneAuth + ValidateCode)
# ============================================================
# Field numbers read from AuthOuterClass$RequestStartPhoneAuth.java,
# RequestValidateCode.java, ResponseAuth.java in the APK decompile.
# Wire format is NOT yet live-verified — Bale's `app_id` + `api_key`
# are the device-attestation credentials the server uses to throttle
# account creation; they're not present in the decompile and must be
# captured from web.bale.ai's bundled JS (or passed in by the user).
#
# Once captured, the flow is:
#   1. StartPhoneAuth → returns transaction_hash, server sends SMS.
#   2. ValidateCode(transaction_hash, code) → returns jwt on success.
#      If the account doesn't exist, returns a flag requiring SignUp.
#
# Service paths (confirmed in ir/nasim/*.java):
#   /bale.auth.v1.Auth/StartPhoneAuth
#   /bale.auth.v1.Auth/ValidateCode
#   /bale.auth.v1.Auth/SignUp

AUTH_SERVICE = "bale.auth.v1.Auth"


@dataclass(frozen=True)
class RequestStartPhoneAuth:
    phone_number: int                   # int64 (no '+', digits only)
    app_id: int                         # int32 — Bale-specific, see docstring
    api_key: str                        # string — Bale-specific
    device_hash: bytes                  # opaque per-install ID
    device_title: str                   # human label e.g. "baleobala"
    time_zone: str = "UTC"
    preferred_languages: tuple = ("en",)
    send_code_type: int = 0             # 0=SMS (default)

    def encode(self) -> bytes:
        out = bytearray()
        out += _enc_tag(1, 0) + _enc_varint(self.phone_number)
        out += _enc_tag(2, 0) + _enc_varint(self.app_id)
        out += _enc_len_delim(3, self.api_key.encode("utf-8"))
        out += _enc_len_delim(4, self.device_hash)
        out += _enc_len_delim(5, self.device_title.encode("utf-8"))
        out += _enc_len_delim(6, self.time_zone.encode("utf-8"))
        for lang in self.preferred_languages:
            out += _enc_len_delim(7, lang.encode("utf-8"))
        if self.send_code_type:
            out += _enc_tag(9, 0) + _enc_varint(self.send_code_type)
        return bytes(out)


@dataclass(frozen=True)
class RequestValidateCode:
    transaction_hash: str              # from StartPhoneAuth response
    code: str                          # 6-digit SMS OTP
    is_jwt: bool = True                # web flow uses JWT

    def encode(self) -> bytes:
        out = bytearray()
        out += _enc_len_delim(1, self.transaction_hash.encode("utf-8"))
        out += _enc_len_delim(2, self.code.encode("utf-8"))
        if self.is_jwt:
            out += _enc_tag(3, 0) + _enc_varint(1)
        return bytes(out)


@dataclass(frozen=True)
class ResponseAuth:
    """ResponseAuth { user(2) | config(3) | jwt(4) }.
    We only need jwt for downstream use; other fields kept raw."""
    jwt: str
    user_blob: bytes = b""
    raw: bytes = b""


def parse_response_auth(buf: bytes) -> ResponseAuth | None:
    """Find the JWT inside a ResponseAuth payload. Returns None if no
    JWT-looking string present (e.g. when SignUp is required)."""
    import re
    # JWT shape: base64url "eyJ..." + . + payload + . + sig.
    m = re.search(rb"(eyJ[A-Za-z0-9_\-.]{50,})", buf)
    if m is None:
        return None
    return ResponseAuth(jwt=m.group(1).decode("ascii"), raw=buf)


def parse_transaction_hash(buf: bytes) -> str | None:
    """ResponseStartPhoneAuth: the only field is a transaction_hash
    string at some tag. Scan for a long hex/base64-ish token."""
    import re
    m = re.search(rb"([A-Za-z0-9_\-]{20,})", buf)
    if m is None:
        return None
    return m.group(1).decode("ascii")


def find_inbound_messages(buf: bytes, *, max_depth: int = 6) -> list[InboundMessage]:
    """Recursively scan an update payload for UpdateMessage-shaped sub-trees.

    Bale wraps pushed updates in one or more outer envelopes (SeqUpdate
    or similar). Without a verified wire capture of the exact shape, we
    descend into every len-delim sub-field and try to parse each as an
    UpdateMessage; anything that yields a TextMessage is kept.
    """
    found: list[InboundMessage] = []

    def visit(b: bytes, depth: int) -> None:
        if depth > max_depth or not b:
            return
        m = _parse_update_message(b)
        if m is not None:
            found.append(m)
        for fn, val, wt in _walk_len_delim(b):
            if wt == 2 and isinstance(val, (bytes, bytearray)) and val:
                visit(val, depth + 1)

    visit(buf, 0)
    # Deduplicate on (rid, peer, text) — the recursive walk can visit
    # the same sub-tree via multiple paths.
    seen: set[tuple[int, int, str]] = set()
    out: list[InboundMessage] = []
    for m in found:
        key = (m.rid, m.peer_user_id, m.text)
        if key in seen:
            continue
        seen.add(key)
        out.append(m)
    return out
