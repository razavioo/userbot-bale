from __future__ import annotations

import base64
import json

from baleobala.bale.protos import (
    RequestStartPhoneAuth,
    RequestValidateCode,
    parse_response_auth,
    parse_transaction_hash,
)


def test_start_phone_auth_contains_key_fields():
    req = RequestStartPhoneAuth(
        phone_number=989120000000,
        app_id=42,
        api_key="test-api-key",
        device_hash=b"\x01" * 16,
        device_title="baleobala-ci",
    )
    buf = req.encode()
    assert b"test-api-key" in buf
    assert b"baleobala-ci" in buf
    assert b"\x01" * 16 in buf


def test_validate_code_minimal():
    req = RequestValidateCode(transaction_hash="tx_abc", code="123456")
    buf = req.encode()
    assert b"tx_abc" in buf
    assert b"123456" in buf


def test_parse_response_auth_finds_jwt():
    # synthesize a ResponseAuth-ish blob with a plausible JWT in it.
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps({"sub": 1}).encode()).rstrip(b"=").decode()
    jwt = f"{header}.{body}.signaturepartxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    blob = b"\x00\x12\x00" + jwt.encode("ascii") + b"\x00trailing"
    auth = parse_response_auth(blob)
    assert auth is not None
    assert auth.jwt == jwt


def test_parse_transaction_hash_finds_token():
    blob = b"\x00\x01junk:ABCDEF1234567890xyzABCDEF:more"
    tx = parse_transaction_hash(blob)
    assert tx == "ABCDEF1234567890xyzABCDEF"
