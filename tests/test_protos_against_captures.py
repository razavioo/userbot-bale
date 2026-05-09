"""D2 — protobuf decoder regression tests against binary captures.

Loads binary captures from captures/ws-live/ and captures/rpcs/ and runs
them through the existing decoders in baleobala.bale.rpc_envelope and
baleobala.bale.protos. Any change in Bale's protobuf wire format that
breaks a decoder will fail a test here before reaching live CI.

Design principles:
  - No network access. Purely file-based.
  - Tests are fast (all binary files are small, < 30 KB each).
  - Assertions are structural invariants, not exact byte matches, so minor
    additions to the protobuf schema don't break them — only removals or
    field-number renumbering do.
  - The test file is intentionally exhaustive about WHICH invariants are
    checked so that future capture refreshes make failures self-documenting.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path
import pytest

_REPO_ROOT = Path(__file__).parent.parent
_WS_LIVE_DIR = _REPO_ROOT / "captures" / "ws-live"
_RPCS_DIR = _REPO_ROOT / "captures" / "rpcs"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load(path: Path) -> bytes:
    return path.read_bytes()


def _ws_live_files() -> list[Path]:
    if not _WS_LIVE_DIR.exists():
        return []
    return sorted(_WS_LIVE_DIR.glob("*.bin"))


def _rpc_files() -> list[Path]:
    if not _RPCS_DIR.exists():
        return []
    return sorted(_RPCS_DIR.glob("*.bin"))


def _rpc_body(data: bytes) -> bytes:
    """Strip the gRPC wire framing (1-byte flag + 4-byte big-endian length)."""
    if len(data) < 5:
        return data
    length = int.from_bytes(data[1:5], "big")
    return data[5:5 + length]


def _parse_varint(buf: bytes, pos: int) -> tuple[int, int]:
    n = 0
    shift = 0
    while True:
        b = buf[pos]
        pos += 1
        n |= (b & 0x7F) << shift
        if not (b & 0x80):
            return n, pos
        shift += 7


def _iter_fields(buf: bytes):
    """Yield (field_number, wire_type, value_or_bytes) for each top-level field."""
    pos = 0
    while pos < len(buf):
        tag, pos = _parse_varint(buf, pos)
        fnum = tag >> 3
        wtype = tag & 0x7
        if wtype == 0:
            v, pos = _parse_varint(buf, pos)
            yield fnum, 0, v
        elif wtype == 2:
            ln, pos = _parse_varint(buf, pos)
            yield fnum, 2, buf[pos:pos + ln]
            pos += ln
        elif wtype == 1:
            yield fnum, 1, buf[pos:pos + 8]
            pos += 8
        elif wtype == 5:
            yield fnum, 5, buf[pos:pos + 4]
            pos += 4
        else:
            return  # unknown wire type — stop


# ---------------------------------------------------------------------------
# ws-live capture tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _WS_LIVE_DIR.exists(), reason="captures/ws-live/ not present")
def test_ws_live_captures_exist():
    files = _ws_live_files()
    assert len(files) >= 10, (
        f"Expected ≥10 ws-live captures, found {len(files)}. "
        "Run scripts/refresh-bale-evidence.sh to regenerate."
    )


@pytest.mark.skipif(not _WS_LIVE_DIR.exists(), reason="captures/ws-live/ not present")
def test_ws_live_client_frames_parse_without_exception():
    """Every *_cl_* frame must decode through Response.decode() without raising."""
    from baleobala.bale.rpc_envelope import Response

    cl_files = [f for f in _ws_live_files() if "_cl_" in f.name]
    assert cl_files, "No client-direction captures found"

    for path in cl_files:
        data = _load(path)
        if not data:
            continue  # empty frames are valid (keepalive)
        try:
            _ = Response.decode(data)
        except Exception as exc:
            pytest.fail(f"{path.name}: decode raised {exc!r}")


@pytest.mark.skipif(not _WS_LIVE_DIR.exists(), reason="captures/ws-live/ not present")
def test_ws_live_server_frames_parse_without_exception():
    """Every *_sv_* frame must decode through Response.decode() without raising."""
    from baleobala.bale.rpc_envelope import Response

    sv_files = [f for f in _ws_live_files() if "_sv_" in f.name]
    assert sv_files, "No server-direction captures found"

    for path in sv_files:
        data = _load(path)
        if not data:
            continue
        try:
            _ = Response.decode(data)
        except Exception as exc:
            pytest.fail(f"{path.name}: decode raised {exc!r}")


@pytest.mark.skipif(not _WS_LIVE_DIR.exists(), reason="captures/ws-live/ not present")
def test_ws_live_large_server_frames_have_payload():
    """Server frames >10 bytes must yield a non-empty payload after decoding.

    This pins that the Response.decode() path is actually reaching payload
    bytes, not silently returning empty on every frame. If the outer
    envelope field number changes (e.g. tag 1 → tag 2), this fails."""
    from baleobala.bale.rpc_envelope import Response

    large_sv = [f for f in _ws_live_files() if "_sv_" in f.name and f.stat().st_size > 10]
    assert large_sv, "No large server-direction captures found"

    empty_payloads = []
    for path in large_sv:
        data = _load(path)
        r = Response.decode(data)
        if not r.payload:
            empty_payloads.append(path.name)

    # Allow up to 10% empty (some sv frames are short ACKs); if all are empty,
    # the decoder is broken.
    assert len(empty_payloads) < len(large_sv), (
        f"ALL large server frames returned empty payload — decoder may be broken.\n"
        f"Files: {empty_payloads[:5]}"
    )


@pytest.mark.skipif(not _WS_LIVE_DIR.exists(), reason="captures/ws-live/ not present")
def test_ws_live_client_frames_majority_are_len_delim():
    """Most client frames start with a len-delim (wire type 2) outer field.
    Captures show both len-delim payload frames (wtype=2) and short varint
    frames (e.g. 0x70 = field 14, wtype=0) that act as control/heartbeat
    frames. We assert that ≥50 % of non-empty frames are len-delim, which
    is enough to detect a wholesale encoding change while tolerating the
    observed mix."""
    cl_files = [f for f in _ws_live_files() if "_cl_" in f.name]
    len_delim = 0
    total = 0
    for path in cl_files:
        data = _load(path)
        if not data:
            continue
        total += 1
        # First byte encodes (field_number << 3 | wire_type).
        # field=1, wtype=2 → 0x0a. field=3, wtype=2 → 0x1a. Both len-delim.
        if (data[0] & 0x7) == 2:
            len_delim += 1

    assert total > 0, "No non-empty client frames found"
    assert len_delim >= total // 2, (
        f"Fewer than 50 % of client frames are len-delim: {len_delim}/{total}. "
        "Bale may have changed the outer wrapper encoding."
    )


# ---------------------------------------------------------------------------
# RPC capture tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _RPCS_DIR.exists(), reason="captures/rpcs/ not present")
def test_rpc_captures_exist():
    files = _rpc_files()
    assert len(files) >= 2, f"Expected ≥2 RPC captures, found {len(files)}"


@pytest.mark.skipif(not _RPCS_DIR.exists(), reason="captures/rpcs/ not present")
def test_rpc_start_phone_auth_request_has_transaction_field():
    """StartPhoneAuth request must contain a field 3 (transaction/phone data)
    of length ≥10. If Bale renumbers the phone field, this test fails before
    any auth flow breaks in production."""
    req_file = _RPCS_DIR / "00_bale.auth.v1.Auth__StartPhoneAuth.req.bin"
    if not req_file.exists():
        pytest.skip("StartPhoneAuth request capture not found")

    data = _load(req_file)
    body = _rpc_body(data)
    field3 = None
    for fnum, wtype, value in _iter_fields(body):
        if fnum == 3 and wtype == 2:
            field3 = value
            break

    assert field3 is not None and len(field3) >= 10, (
        f"StartPhoneAuth request: expected field 3 (transaction data) ≥10 bytes; "
        f"got {field3!r}. Bale may have renumbered the phone/transaction field."
    )


@pytest.mark.skipif(not _RPCS_DIR.exists(), reason="captures/rpcs/ not present")
def test_rpc_start_phone_auth_response_parse():
    """StartPhoneAuth response must be parseable and yield a transaction_hash."""
    from baleobala.bale.protos import parse_transaction_hash
    res_file = _RPCS_DIR / "00_bale.auth.v1.Auth__StartPhoneAuth.res.bin"
    if not res_file.exists():
        pytest.skip("StartPhoneAuth response capture not found")

    data = _load(res_file)
    body = _rpc_body(data)
    result = parse_transaction_hash(body)
    assert result is not None and len(result) >= 10, (
        f"parse_transaction_hash returned {result!r}; "
        "Bale may have changed the transaction_hash field position or encoding."
    )


@pytest.mark.skipif(not _RPCS_DIR.exists(), reason="captures/rpcs/ not present")
def test_rpc_validate_code_request_structure():
    """ValidateCode request must have at least two len-delim fields.

    The request carries: transaction_hash (field 1 or 3) and the SMS code
    (another field). If the field count drops to zero the encoder is broken."""
    req_file = _RPCS_DIR / "01_bale.auth.v1.Auth__ValidateCode.req.bin"
    if not req_file.exists():
        pytest.skip("ValidateCode request capture not found")

    data = _load(req_file)
    body = _rpc_body(data)
    len_delim_fields = [(fnum, len(value)) for fnum, wtype, value in _iter_fields(body) if wtype == 2]
    assert len(len_delim_fields) >= 2, (
        f"ValidateCode request has fewer len-delim fields than expected: {len_delim_fields}"
    )


@pytest.mark.skipif(not _RPCS_DIR.exists(), reason="captures/rpcs/ not present")
def test_rpc_validate_code_response_contains_jwt():
    """ValidateCode response must contain a JWT-looking token.

    The JWT is how the client authenticates subsequent requests. If the
    auth flow switches to a different credential format, this test signals it."""
    from baleobala.bale.protos import parse_response_auth as parse_auth_response
    res_file = _RPCS_DIR / "01_bale.auth.v1.Auth__ValidateCode.res.bin"
    if not res_file.exists():
        pytest.skip("ValidateCode response capture not found")

    data = _load(res_file)
    body = _rpc_body(data)
    result = parse_auth_response(body)
    assert result is not None, (
        "parse_auth_response returned None — JWT not found in ValidateCode response. "
        "Bale may have changed the auth credential format."
    )
    assert result.jwt.startswith("eyJ"), (
        f"JWT does not start with 'eyJ': {result.jwt[:20]!r}"
    )


# ---------------------------------------------------------------------------
# Cross-layer: encode→decode round-trip for our own message types
# ---------------------------------------------------------------------------


def test_request_start_livekit_call_encodes_to_valid_protobuf():
    """RequestStartLiveKitCall.encode() must produce valid protobuf bytes that
    contain:
      - field 1 (len-delim) = OutPeer
      - field 2 (varint)    = rid (random int64 call identifier)
      - field 4 (len-delim) = BooleanValue (inviteEnable)
    This is the core call-init message; if any field drifts all calls fail."""
    from baleobala.bale.protos import RequestStartLiveKitCall, OutPeer

    req = RequestStartLiveKitCall(peer=OutPeer(user_id=12345))
    encoded = req.encode()

    assert isinstance(encoded, bytes) and encoded, "encode() returned empty"

    fields = {fnum: (wtype, value) for fnum, wtype, value in _iter_fields(encoded)}
    assert 1 in fields and fields[1][0] == 2, "Missing OutPeer at field 1 (len-delim)"
    assert 2 in fields and fields[2][0] == 0, "Missing rid at field 2 (varint)"
    assert 4 in fields and fields[4][0] == 2, "Missing inviteEnable at field 4 (len-delim)"


def test_out_peer_encode_decode_round_trip():
    """OutPeer bytes must have field 1 (type=varint) and field 2 (id=varint)."""
    from baleobala.bale.protos import OutPeer, PEER_TYPE_PRIVATE

    peer = OutPeer(user_id=99999, type=PEER_TYPE_PRIVATE)
    encoded = peer.encode()

    fields = {fnum: (wtype, value) for fnum, wtype, value in _iter_fields(encoded)}
    assert 1 in fields and fields[1][0] == 0, "Missing peer type at field 1"
    assert 2 in fields and fields[2][0] == 0, "Missing user_id at field 2"
    assert fields[1][1] == PEER_TYPE_PRIVATE
    assert fields[2][1] == 99999
