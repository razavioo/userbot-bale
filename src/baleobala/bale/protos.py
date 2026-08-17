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
    peer_id: int | None = None
    raw: bytes = b""


@dataclass(frozen=True)
class IncomingCallEvent:
    credentials: CallCredentials
    peer_id: int | None
    source: str = "push"


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


def _find_fields(buf: bytes, field_num: int) -> list[bytes]:
    """Return every length-delimited value of `field_num` anywhere in
    `buf` (including nested length-delim submessages).

    We don't know the exact nesting of Bale's push envelope, so we
    walk depth-first and collect every matching len-delim field. Safe
    on malformed input: parse errors are swallowed.
    """
    out: list[bytes] = []
    pos = 0
    n = len(buf)
    while pos < n:
        try:
            tag, pos = _dec_varint(buf, pos)
            fn, wt = tag >> 3, tag & 7
            if wt == 2:
                length, pos = _dec_varint(buf, pos)
                if length < 0 or pos + length > n:
                    return out
                inner = buf[pos:pos + length]
                pos += length
                if fn == field_num:
                    out.append(inner)
                else:
                    out.extend(_find_fields(inner, field_num))
            elif wt == 0:
                _, pos = _dec_varint(buf, pos)
            elif wt == 1:
                pos += 8
            elif wt == 5:
                pos += 4
            else:
                return out
        except (IndexError, ValueError):
            return out
    return out


# MeetOuterClass.UpdateCallReceived is wrapped at tag 52810 inside the
# SetUpdatesStruct.ComposedUpdates union (seen in
# re/jadx-out/sources/ai/bale/proto/SetUpdatesStruct$ComposedUpdates.java).
# The inner message has field 1 = callId (int64).
_UPDATE_CALL_RECEIVED_TAG = 52810


def parse_update_call_received(buf: bytes) -> int | None:
    """Return the callId from an UpdateCallReceived push, or None if
    the push doesn't contain one."""
    for inner in _find_fields(buf, _UPDATE_CALL_RECEIVED_TAG):
        # UpdateCallReceived { 1: callId (varint) }
        pos = 0
        while pos < len(inner):
            try:
                tag, pos = _dec_varint(inner, pos)
                fn, wt = tag >> 3, tag & 7
                if fn == 1 and wt == 0:
                    call_id, _ = _dec_varint(inner, pos)
                    return call_id
                if wt == 0:
                    _, pos = _dec_varint(inner, pos)
                elif wt == 2:
                    ln, pos = _dec_varint(inner, pos)
                    pos += ln
                elif wt == 1:
                    pos += 8
                elif wt == 5:
                    pos += 4
                else:
                    break
            except (IndexError, ValueError):
                break
    return None


def parse_incoming_call_offer(buf: bytes) -> int | None:
    """Extract callId from the short incoming-call offer push.

    Live-probed 2026-04-23 on the callee account: Bale may push a
    compact call offer that contains the room UUID and wss URL but no
    JWT token. The nested payload starts with field 1 = callId and also
    includes the room (field 3) + URL (field 4). This helper lets the
    callee AcceptCall immediately instead of waiting for a later
    UpdateCallReceived variant that may never arrive.
    """
    room_pos = buf.find(b"\x1a$")
    url_pos = buf.find(b"wss://meet-")
    if room_pos == -1 or url_pos == -1:
        return None

    for start in range(room_pos - 2, -1, -1):
        if buf[start] != 0x0A or start + 1 >= len(buf):
            continue
        # Decode the field length as a proper varint (not a single byte):
        # the StartCall inline response uses 2-byte lengths (e.g. 0xfe 0x04
        # for 638), so reading only buf[start+1] gave the wrong inner_end,
        # causing the containment check to fail for all outgoing call responses.
        try:
            length, inner_start = _dec_varint(buf, start + 1)
        except (IndexError, ValueError):
            continue
        inner_end = inner_start + length
        if inner_end > len(buf):
            continue
        if not (inner_start <= room_pos < inner_end and inner_start <= url_pos < inner_end):
            continue
        inner = buf[inner_start:inner_end]
        if not inner.startswith(b"\x08"):
            continue
        try:
            call_id, _ = _dec_varint(inner, 1)
            return call_id
        except (IndexError, ValueError):
            continue
    return None


# MeetOuterClass.RequestAcceptCall
#   field 1 (varint)    = callId (int64)
#   field 2 (len-delim) = inviteEnable (BooleanValue { field 1 = 1 })
# Service/method:       /bale.meet.v1.Meet/AcceptCall
MEET_SERVICE = "bale.meet.v1.Meet"
ACCEPT_CALL_METHOD = "AcceptCall"


def encode_accept_call(call_id: int, invite_enable: bool = True) -> bytes:
    body = bytearray()
    body += _enc_tag(1, 0) + _enc_varint(call_id)
    if invite_enable:
        body += _enc_len_delim(2, _enc_bool_value(True))
    # Live-probed 2026-04-23: unlike StartCall, AcceptCall expects the
    # raw RequestAcceptCall body, not a bale.meet outer tag-6 wrapper.
    return bytes(body)


_UUID_PAT = rb'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
_UUID_LEN = 36


def _extract_room_from_buf(buf: bytes) -> str:
    """Extract the LiveKit room UUID from a Bale push/response payload.

    Uses two strategies, in order of reliability:

    1. **Field-3 tag probe** (`\\x1a\\x24`): tag byte 0x1a = field 3 wiretype 2
       (len-delim), length byte 0x24 = 36 (exact UUID string length). The 36
       bytes that follow are the room name string. This anchor is confirmed in
       `parse_incoming_call_offer` (live-probed 2026-04-23) and is
       structure-aware — it won't pick up other UUIDs that happen to appear in
       the payload (e.g. call-IDs, session tokens).

    2. **Generic UUID regex fallback**: matches the first UUID-shaped string
       anywhere in the buffer. Used when the field-3 anchor is absent (e.g.
       older payload shapes).
    """
    import re
    # Strategy 1: field 3 anchor (reliable, structure-aware)
    pos = 0
    while True:
        idx = buf.find(b"\x1a\x24", pos)
        if idx == -1:
            break
        candidate = buf[idx + 2: idx + 2 + _UUID_LEN]
        if len(candidate) == _UUID_LEN and re.match(_UUID_PAT, candidate):
            return candidate.decode("ascii")
        pos = idx + 1
    # Strategy 2: first UUID regex fallback
    m = re.search(_UUID_PAT, buf)
    return m.group(0).decode("ascii") if m else ""


def parse_call_credentials(buf: bytes) -> CallCredentials | None:
    """Extract LiveKit url + JWT + room out of an opaque server update.

    The message layout is nested protobuf (sub-messages at tags we
    haven't all mapped), but the url + token + room uuid appear as
    length-prefixed strings at predictable positions. We scan byte-wise.
    """
    import re
    url_m = re.search(rb'(wss://[a-zA-Z0-9./\-]+\.(?:ir|ai))', buf)
    tok_m = re.search(rb'(eyJhbGciOi[A-Za-z0-9_\-.]{100,})', buf)
    if not (url_m and tok_m):
        return None
    peer_id = parse_call_peer_id(buf)
    return CallCredentials(
        url=url_m.group(0).decode("ascii"),
        token=tok_m.group(0).decode("ascii"),
        room=_extract_room_from_buf(buf),
        peer_id=peer_id,
        raw=buf,
    )


def parse_call_peer_id(buf: bytes) -> int | None:
    """Best-effort extraction of a Bale user_id from call-related pushes.

    We look for nested `OutPeer`-shaped messages (`field 1 = type`,
    `field 2 = user_id`) near the credentials payload and return the
    first plausible user id.
    """
    candidates: list[int] = []

    def _scan(inner: bytes) -> None:
        pos = 0
        peer_type = None
        user_id = None
        def _commit_candidate() -> None:
            if peer_type in {1, 2} and user_id is not None and 10_000 <= user_id <= 5_000_000_000:
                candidates.append(user_id)

        while pos < len(inner):
            try:
                tag, pos = _dec_varint(inner, pos)
            except (IndexError, ValueError):
                _commit_candidate()
                return
            fn, wt = tag >> 3, tag & 7
            if wt == 0:
                try:
                    val, pos = _dec_varint(inner, pos)
                except (IndexError, ValueError):
                    _commit_candidate()
                    return
                if fn == 1:
                    peer_type = val
                elif fn == 2:
                    user_id = val
            elif wt == 2:
                try:
                    ln, pos = _dec_varint(inner, pos)
                except (IndexError, ValueError):
                    _commit_candidate()
                    return
                end = pos + ln
                if end > len(inner):
                    end = len(inner)
                _scan(inner[pos:end])
                pos = end
            elif wt == 1:
                pos += 8
            elif wt == 5:
                pos += 4
            else:
                _commit_candidate()
                return
        _commit_candidate()

    _scan(buf)
    return candidates[0] if candidates else None


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
# Web-platform credentials extracted 2026-04-20 from web.bale.ai's
# bundled index.js — public client constants, not secrets. Other
# platforms: iOS is app_id=2, Android has its own pair; use whichever
# your account was created on.
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

# Web client defaults (observed in web.bale.ai index.js, 2026-04-20).
# These are public platform identifiers, same across all web users.
WEB_APP_ID = 4
WEB_API_KEY = "C28D46DC4C3A7A26564BFCC48B929086A95C93C98E789A19847BEE8627DE4E7D"


@dataclass(frozen=True)
class RequestStartPhoneAuth:
    """Matches the exact shape web.bale.ai sends (verified from
    bundled JS: `this.api.StartPhoneAuth({phoneNumber, deviceTitle,
    sendCodeType, apiKey, appId, deviceHash, timeZone:void 0,
    imeiList:void 0, preferredLanguages:[], options})`).

    time_zone, preferred_languages, imei_list are intentionally
    omitted from the wire — the web client sends them as undefined
    / empty which protobuf3 encodes as absent. Our encoder follows
    that shape. If you need to override on a non-web account, pass
    a non-empty value and the field will be emitted."""

    phone_number: int                   # int64 (no '+', digits only)
    app_id: int = WEB_APP_ID
    api_key: str = WEB_API_KEY
    device_hash: bytes = b""
    device_title: str = "baleobala"
    time_zone: str = ""                 # empty => omitted
    preferred_languages: tuple = ()     # empty => omitted
    send_code_type: int = 0
    options: int = 0                    # 0=SUPPORT_TELEGRAM_GATEWAY

    def encode(self) -> bytes:
        out = bytearray()
        out += _enc_tag(1, 0) + _enc_varint(self.phone_number)
        out += _enc_tag(2, 0) + _enc_varint(self.app_id)
        out += _enc_len_delim(3, self.api_key.encode("utf-8"))
        out += _enc_len_delim(4, self.device_hash)
        out += _enc_len_delim(5, self.device_title.encode("utf-8"))
        if self.time_zone:
            out += _enc_len_delim(6, self.time_zone.encode("utf-8"))
        for lang in self.preferred_languages:
            out += _enc_len_delim(7, lang.encode("utf-8"))
        if self.send_code_type:
            out += _enc_tag(9, 0) + _enc_varint(self.send_code_type)
        if self.options:
            out += _enc_tag(10, 0) + _enc_varint(self.options)
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


def parse_user_id_from_auth_response(buf: bytes) -> int | None:
    """Scan ResponseAuth for the user_id (a large int, typically 1M-5B range).

    The ValidateCode response carries a User sub-message whose first
    varint field is the user_id. We scan all varint fields for values
    in the plausible Bale user_id range (10_000 – 5_000_000_000).
    The first match is returned."""
    candidates: list[int] = []
    for fn, val, wt in _walk_len_delim(buf):
        if wt == 0 and 10_000 <= val <= 5_000_000_000:
            candidates.append(val)
        elif wt == 2 and isinstance(val, (bytes, bytearray)) and val:
            for _, v2, w2 in _walk_len_delim(val):
                if w2 == 0 and 10_000 <= v2 <= 5_000_000_000:
                    candidates.append(v2)
    return candidates[0] if candidates else None


class RequestGetJWTToken:
    """bale.auth.v1.Auth/GetJWTToken — empty request body.
    The server identifies the session via session_id + user_id headers."""

    def encode(self) -> bytes:
        return b""


@dataclass(frozen=True)
class RequestSignUp:
    """RequestSignUp — sent after ValidateCode for a phone that has no
    Bale account yet. Field numbers verified against the web bundle's
    SignUp request encoder and the Android port (BaleProtos.kt):
        1: transaction_hash (string)
        2: name             (string)
        3: sex              (int32 enum; 0=UNKNOWN, omitted)
        4: password         (google.protobuf.StringValue; omitted unless set)
    The server replies with the same ResponseAuth shape as ValidateCode.
    """

    transaction_hash: str
    name: str
    sex: int = 0
    password: str | None = None

    def encode(self) -> bytes:
        out = bytearray()
        if self.transaction_hash:
            out += _enc_len_delim(1, self.transaction_hash.encode("utf-8"))
        if self.name:
            out += _enc_len_delim(2, self.name.encode("utf-8"))
        if self.sex:
            out += _enc_tag(3, 0) + _enc_varint(self.sex)
        if self.password is not None:
            inner = _enc_len_delim(1, self.password.encode("utf-8"))
            out += _enc_len_delim(4, bytes(inner))
        return bytes(out)


def parse_get_jwt_token_response(buf: bytes) -> str | None:
    """ResponseGetJWTToken { jwt: StringValue }.
    StringValue wraps a string at field 1 of the inner message;
    the outer response carries it at field 1 as a len-delim.
    Falls back to a regex scan so we're not brittle to field number changes."""
    import re
    m = re.search(rb"(eyJ[A-Za-z0-9_\-.]{50,})", buf)
    if m is None:
        return None
    return m.group(1).decode("ascii")


def parse_transaction_hash(buf: bytes) -> str | None:
    """ResponseStartPhoneAuth: the only field is a transaction_hash
    string at some tag. Scan for a long hex/base64-ish token."""
    import re
    m = re.search(rb"([A-Za-z0-9_\-]{20,})", buf)
    if m is None:
        return None
    return m.group(1).decode("ascii")


# ============================================================
# Meet: ReceiveCall, GetWssURL, JoinGroupCall, LeaveGroupCall
# ============================================================
# Field numbers from:
#   re/jadx-out/sources/ai/bale/proto/MeetOuterClass$RequestReceiveCall.java
#   re/jadx-out/sources/ai/bale/proto/MeetOuterClass$RequestGetWssURL.java
#   re/jadx-out/sources/ai/bale/proto/MeetOuterClass$ResponseGetWssURL.java
#   re/jadx-out/sources/ai/bale/proto/MeetOuterClass$RequestJoinGroupCall.java
#   re/jadx-out/sources/ai/bale/proto/MeetOuterClass$RequestLeaveGroupCall.java
# Service: bale.meet.v1.Meet
# NOTE: outer RPC tag wrapping unverified for these methods — no outer wrapper
# sent (same as AcceptCall). Probe live and add _enc_len_delim wrapper if
# the server rejects with "unknown method".

RECEIVE_CALL_METHOD = "ReceiveCall"
GET_WSS_URL_METHOD = "GetWssURL"
JOIN_GROUP_CALL_METHOD = "JoinGroupCall"
LEAVE_GROUP_CALL_METHOD = "LeaveGroupCall"


@dataclass(frozen=True)
class RequestReceiveCall:
    """MeetOuterClass.RequestReceiveCall — field 1: callId (int64)."""
    call_id: int

    def encode(self) -> bytes:
        return _enc_tag(1, 0) + _enc_varint(self.call_id)


@dataclass(frozen=True)
class RequestGetWssURL:
    """MeetOuterClass.RequestGetWssURL — field 1: callId (int64)."""
    call_id: int

    def encode(self) -> bytes:
        return _enc_tag(1, 0) + _enc_varint(self.call_id)


def parse_get_wss_url_response(buf: bytes) -> str | None:
    """ResponseGetWssURL — field 1: url (string).

    Uses structural field-1 scan first; falls back to wss:// regex."""
    import re
    pos = 0
    while pos < len(buf):
        try:
            fn, wt, pos = _dec_tag(buf, pos)
            if wt == 2:
                ln, pos = _dec_varint(buf, pos)
                val = buf[pos:pos + ln]
                pos += ln
                if fn == 1:
                    try:
                        s = val.decode("utf-8")
                        if s.startswith("wss://"):
                            return s
                    except UnicodeDecodeError:
                        pass
            elif wt == 0:
                _, pos = _dec_varint(buf, pos)
            else:
                break
        except (IndexError, ValueError):
            break
    m = re.search(rb'(wss://[a-zA-Z0-9./\-]+\.(?:ir|ai))', buf)
    return m.group(0).decode("ascii") if m else None


@dataclass(frozen=True)
class RequestJoinGroupCall:
    """MeetOuterClass.RequestJoinGroupCall
        field 1: callId (int64)
        field 2: name   (StringValue { field 1: string })
    """
    call_id: int
    name: str = ""

    def encode(self) -> bytes:
        out = bytearray()
        out += _enc_tag(1, 0) + _enc_varint(self.call_id)
        if self.name:
            name_sv = _enc_len_delim(1, self.name.encode("utf-8"))
            out += _enc_len_delim(2, bytes(name_sv))
        return bytes(out)


@dataclass(frozen=True)
class RequestLeaveGroupCall:
    """MeetOuterClass.RequestLeaveGroupCall
        field 1: callId (int64)
        field 2: end    (bool) — True terminates the call for everyone
    """
    call_id: int
    end: bool = False

    def encode(self) -> bytes:
        out = bytearray()
        out += _enc_tag(1, 0) + _enc_varint(self.call_id)
        if self.end:
            out += _enc_tag(2, 0) + _enc_varint(1)
        return bytes(out)


# ============================================================
# Messaging: LoadHistory, LoadDialogs, MessageRead
# ============================================================
# Field numbers from:
#   re/jadx-out/sources/ai/bale/proto/MessagingOuterClass$RequestLoadHistory.java
#   re/jadx-out/sources/ai/bale/proto/MessagingOuterClass$RequestLoadDialogs.java
#   re/jadx-out/sources/ai/bale/proto/MessagingOuterClass$RequestMessageRead.java
# Service: bale.messaging.v2.Messaging
# NOTE: outer RPC tag unverified — no wrapper used; correct if server rejects.

LOAD_HISTORY_METHOD = "LoadHistory"
LOAD_DIALOGS_METHOD = "LoadDialogs"
MESSAGE_READ_METHOD = "MessageRead"


@dataclass(frozen=True)
class RequestLoadHistory:
    """MessagingOuterClass.RequestLoadHistory
        field 1: peer     (OutPeer)
        field 2: date     (int64; 0 = from newest)
        field 4: loadMode (varint enum; 0=FORWARD)
        field 5: limit    (int32)
    """
    peer: OutPeer
    date: int = 0
    load_mode: int = 0
    limit: int = 20

    def encode(self) -> bytes:
        out = bytearray()
        out += _enc_len_delim(1, self.peer.encode())
        if self.date:
            out += _enc_tag(2, 0) + _enc_varint(self.date)
        if self.load_mode:
            out += _enc_tag(4, 0) + _enc_varint(self.load_mode)
        out += _enc_tag(5, 0) + _enc_varint(self.limit)
        return bytes(out)


@dataclass(frozen=True)
class HistoryMessage:
    rid: int
    sender_uid: int
    date: int
    text: str | None


def parse_load_history_response(buf: bytes) -> list[HistoryMessage]:
    """Parse ``ResponseLoadHistory.history``.

    APK schema (Bale 2026-08-17): ``ResponseLoadHistory.history`` is
    repeated field 1, containing ``MessageContainer`` messages. Its
    relevant fields are ``sender_uid=1``, ``rid=2``, ``date=3``, and
    ``message=4``. Keep the parser scoped to field 1 so user/group
    metadata elsewhere in the response cannot be reported as a message.
    """

    def _try_container(b: bytes) -> HistoryMessage | None:
        rid = sender_uid = date = 0
        text: str | None = None
        for fn, val, wt in _walk_len_delim(b):
            if wt == 0:
                if fn == 1:
                    sender_uid = val
                elif fn == 2:
                    rid = val
                elif fn == 3:
                    date = val
            elif wt == 2 and fn == 4:
                text = _parse_text_from_message(val)
        if rid and text is not None:
            return HistoryMessage(rid=rid, sender_uid=sender_uid, date=date, text=text)
        return None

    out: list[HistoryMessage] = []
    for fn, val, wt in _walk_len_delim(buf):
        if fn == 1 and wt == 2 and isinstance(val, (bytes, bytearray)):
            msg = _try_container(val)
            if msg is not None:
                out.append(msg)
    return out


@dataclass(frozen=True)
class RequestLoadDialogs:
    """MessagingOuterClass.RequestLoadDialogs
        field 1: minDate (int64; 0 = all)
        field 2: limit   (int32)
    Remaining fields (dialogType, excludePinnedDialogs, archiveFilter)
    omitted — server defaults are fine for peer discovery.
    """
    min_date: int = 0
    limit: int = 20

    def encode(self) -> bytes:
        out = bytearray()
        if self.min_date:
            out += _enc_tag(1, 0) + _enc_varint(self.min_date)
        out += _enc_tag(2, 0) + _enc_varint(self.limit)
        return bytes(out)


@dataclass(frozen=True)
class DialogInfo:
    peer_id: int
    peer_type: int
    unread_count: int
    last_message_date: int


def parse_load_dialogs_response(buf: bytes) -> list[DialogInfo]:
    """Parse ``ResponseLoadDialogs.dialogs``.

    APK schema (Bale 2026-08-17): dialogs are repeated field 3 of the
    response. A ``Dialog`` has ``peer=1`` (``Peer {type=1, id=2}``),
    ``unread_count=2``, and ``date=6``. The response's other repeated
    fields contain users and groups, so they must not be treated as dialogs.
    """
    out: list[DialogInfo] = []

    def _try_dialog(b: bytes) -> DialogInfo | None:
        peer_type = peer_id = unread = date = 0
        for fn, val, wt in _walk_len_delim(b):
            if wt == 2 and fn == 1:
                pt, pid = _parse_out_peer(val)
                peer_type, peer_id = pt, pid
            elif wt == 0:
                if fn == 2:
                    unread = val
                elif fn == 6:
                    date = val
        if peer_id:
            return DialogInfo(
                peer_id=peer_id, peer_type=peer_type,
                unread_count=unread, last_message_date=date,
            )
        return None

    for fn, val, wt in _walk_len_delim(buf):
        if fn == 3 and wt == 2 and isinstance(val, (bytes, bytearray)):
            d = _try_dialog(val)
            if d is not None:
                out.append(d)
    return out


@dataclass(frozen=True)
class RequestMessageRead:
    """MessagingOuterClass.RequestMessageRead
        field 1: peer (OutPeer) — deprecated but still accepted
        field 2: date (int64)   — timestamp of newest message to mark read
    field 3 (ExPeer) omitted; server accepts without it.
    """
    peer: OutPeer
    date: int

    def encode(self) -> bytes:
        out = bytearray()
        out += _enc_len_delim(1, self.peer.encode())
        out += _enc_tag(2, 0) + _enc_varint(self.date)
        return bytes(out)


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
