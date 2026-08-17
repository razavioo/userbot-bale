"""
Bale RPC envelope format (observed over wss://next-ws.bale.ai/ws/).

Each WebSocket binary frame carries a protobuf-encoded RPC envelope of
the shape:

    message Request {           // wrapped at outer tag 1
        string service     = 1;
        string method      = 2;
        bytes  payload     = 3;
        map<string,bytes> metadata = 4;
        int32  seq         = 5;
    }

    message Response {           // at outer tag 2 (server-initiated) or 1
        ...inner envelope echoing seq + payload
    }

Reverse-engineered from a mitmproxy capture of the official
web.bale.ai client on 2026-04-19. See docs/CAPTURE.md.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict

log = logging.getLogger(__name__)


def _enc_varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _enc_tag(field_number: int, wire_type: int) -> bytes:
    return _enc_varint((field_number << 3) | wire_type)


def _enc_len_delim(field_number: int, data: bytes) -> bytes:
    return _enc_tag(field_number, 2) + _enc_varint(len(data)) + data


def _enc_string(field_number: int, s: str) -> bytes:
    return _enc_len_delim(field_number, s.encode("utf-8"))


def _dec_varint(buf: bytes, pos: int) -> tuple[int, int]:
    n = 0
    shift = 0
    while True:
        b = buf[pos]
        pos += 1
        n |= (b & 0x7F) << shift
        if not (b & 0x80):
            return n, pos
        shift += 7


def _dec_tag(buf: bytes, pos: int) -> tuple[int, int, int]:
    tag, pos = _dec_varint(buf, pos)
    return (tag >> 3), (tag & 7), pos


# Client info headers observed on every WS RPC from web.bale.ai. The web
# client keeps one timestamp-like session id for the life of its page and
# includes it twice (once under the ``mt_`` compatibility name) on every
# request. Keep the same shape so read and write RPCs share one consistent
# session fingerprint.
DEFAULT_SESSION_ID = str(int(time.time() * 1_000))

DEFAULT_METADATA: Dict[str, str] = {
    "app_version": "151668",
    "browser_type": "1",
    "browser_version": "147.0.0.0",
    "os_type": "4",
    "mt_app_version": "151668",
    "mt_browser_type": "1",
    "mt_browser_version": "147.0.0.0",
    "mt_os_type": "4",
    "session_id": DEFAULT_SESSION_ID,
    "mt_session_id": DEFAULT_SESSION_ID,
}


def _resolve_metadata() -> Dict[str, str]:
    """Allow operators to override the per-RPC client fingerprint via
    BALE_RPC_METADATA_OVERRIDE (a JSON object). Useful for matching the
    current web client's exact strings when DEFAULT_METADATA goes stale,
    without code changes. Invalid JSON falls back to DEFAULT_METADATA
    with a warning."""
    raw = os.environ.get("BALE_RPC_METADATA_OVERRIDE")
    if not raw:
        return dict(DEFAULT_METADATA)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning(
            "BALE_RPC_METADATA_OVERRIDE is not valid JSON (%s); using defaults",
            exc,
        )
        return dict(DEFAULT_METADATA)
    if not isinstance(parsed, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()
    ):
        log.warning(
            "BALE_RPC_METADATA_OVERRIDE must be a flat {string: string} map; "
            "using defaults",
        )
        return dict(DEFAULT_METADATA)
    merged = dict(DEFAULT_METADATA)
    merged.update(parsed)
    return merged


def _enc_metadata(md: Dict[str, str]) -> bytes:
    """Metadata map serialization.

    Wire layout observed in captures:
        tag 4 (outer len-delim) {
            repeated tag 1 (entry len-delim) {
                field 1 = key (string)
                field 2 (len-delim) {
                    field 1 = value (bytes/string)
                }
            }
        }
    """
    entries = bytearray()
    for k, v in md.items():
        key_bytes = _enc_string(1, k)
        val_wrap = _enc_len_delim(2, _enc_string(1, v))
        entries += _enc_len_delim(1, key_bytes + val_wrap)
    return _enc_len_delim(4, bytes(entries))


@dataclass
class Request:
    service: str
    method: str
    payload: bytes = b""
    metadata: Dict[str, str] = field(default_factory=_resolve_metadata)
    seq: int | None = None

    def encode(self) -> bytes:
        body = bytearray()
        body += _enc_string(1, self.service)
        body += _enc_string(2, self.method)
        if self.payload:
            body += _enc_len_delim(3, self.payload)
        body += _enc_metadata(self.metadata)
        if self.seq is not None:
            body += _enc_tag(5, 0) + _enc_varint(self.seq)
        return _enc_len_delim(1, bytes(body))


@dataclass
class Response:
    seq: int | None
    payload: bytes
    raw: bytes = b""

    @classmethod
    def decode(cls, buf: bytes) -> "Response":
        """Decode a server WS frame.

        Observed layout:
            outer tag 1 wraps response:
                field 2 (bytes)  = payload
                field 3 (varint) = seq echo (of the request's seq)
        Also tolerates the client-side tag 5 seq variant in case the
        server ever uses it.
        """
        pos = 0
        seq: int | None = None
        payload = b""
        while pos < len(buf):
            fnum, wtype, pos = _dec_tag(buf, pos)
            if wtype == 0:
                n, pos = _dec_varint(buf, pos)
                if fnum in (3, 5):
                    seq = n
            elif wtype == 2:
                length, pos = _dec_varint(buf, pos)
                inner = buf[pos:pos + length]
                pos += length
                if fnum == 1:
                    sub = cls.decode(inner)
                    if sub.seq is not None and seq is None:
                        seq = sub.seq
                    if sub.payload:
                        payload = sub.payload
                elif fnum == 2:
                    payload = inner
            elif wtype == 5:
                pos += 4
            elif wtype == 1:
                pos += 8
            else:
                break
        return cls(seq=seq, payload=payload, raw=buf)
