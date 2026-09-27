from __future__ import annotations

import pytest

from userbot_bale.bale.auth import BaleAuth, BaleCodeChannelUnavailable
from userbot_bale.bale.protos import (
    SEND_CODE_TYPE_BALEONLY,
    SEND_CODE_TYPE_DEFAULT,
    SEND_CODE_TYPE_SMS,
    RequestStartPhoneAuth,
    parse_response_start_phone_auth,
    parse_transaction_hash,
)
from userbot_bale.bale.rpc_envelope import _enc_len_delim, _enc_tag, _enc_varint


def _encode_start_response(
    *,
    tx: str = "transaction_hash_abcdef1234567890",
    sent_code_type: int = SEND_CODE_TYPE_BALEONLY,
    available: tuple[int, ...] = (),
    is_registered: bool = True,
) -> bytes:
    out = bytearray()
    out += _enc_len_delim(1, tx.encode("utf-8"))
    if is_registered:
        out += _enc_tag(2, 0) + _enc_varint(1)
    if sent_code_type:
        out += _enc_tag(5, 0) + _enc_varint(sent_code_type)
    if available:
        # packed repeated int32 at field 11 (wire type 2)
        packed = b"".join(_enc_varint(v) for v in available)
        out += _enc_len_delim(11, packed)
    return bytes(out)


def test_start_phone_auth_encodes_baleonly_send_code_type():
    req = RequestStartPhoneAuth(
        phone_number=989120000000,
        app_id=4,
        api_key="key",
        device_hash=b"dev",
        device_title="userbot-bale-ci",
        send_code_type=SEND_CODE_TYPE_BALEONLY,
    )
    buf = req.encode()
    # field 9 varint 2 → tag (9<<3)|0 = 72 = 0x48, value 0x02
    assert bytes.fromhex("4802") in buf


def test_start_phone_auth_omits_default_send_code_type():
    req = RequestStartPhoneAuth(
        phone_number=989120000000,
        send_code_type=SEND_CODE_TYPE_DEFAULT,
    )
    # DEFAULT is 1 — still encoded when explicitly set to non-zero 1;
    # only 0 is omitted.
    buf_default = req.encode()
    assert bytes.fromhex("4801") in buf_default

    req0 = RequestStartPhoneAuth(
        phone_number=989120000000,
        send_code_type=0,
    )
    assert bytes.fromhex("4801") not in req0.encode()
    assert bytes.fromhex("4802") not in req0.encode()


def test_parse_response_start_phone_auth_fields():
    body = _encode_start_response(
        tx="tx_abcdefghijklmnopqrstuv",
        sent_code_type=SEND_CODE_TYPE_BALEONLY,
        available=(SEND_CODE_TYPE_BALEONLY, SEND_CODE_TYPE_SMS),
    )
    parsed = parse_response_start_phone_auth(body)
    assert parsed.transaction_hash == "tx_abcdefghijklmnopqrstuv"
    assert parsed.sent_code_type == SEND_CODE_TYPE_BALEONLY
    assert parsed.available_send_code_types == (
        SEND_CODE_TYPE_BALEONLY,
        SEND_CODE_TYPE_SMS,
    )
    assert parsed.is_registered is True
    # legacy regex fallback still works on the same blob
    assert parse_transaction_hash(body) == "tx_abcdefghijklmnopqrstuv"


def test_parse_response_available_unpacked_varints():
    body = bytearray()
    body += _enc_len_delim(1, b"tx_unpacked_fields_value_ok")
    body += _enc_tag(5, 0) + _enc_varint(SEND_CODE_TYPE_SMS)
    body += _enc_tag(11, 0) + _enc_varint(SEND_CODE_TYPE_BALEONLY)
    body += _enc_tag(11, 0) + _enc_varint(SEND_CODE_TYPE_SMS)
    parsed = parse_response_start_phone_auth(bytes(body))
    assert parsed.sent_code_type == SEND_CODE_TYPE_SMS
    assert parsed.available_send_code_types == (
        SEND_CODE_TYPE_BALEONLY,
        SEND_CODE_TYPE_SMS,
    )


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.set_cookies: list = []
        self.http_status = 200


class _FakeClient:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self.calls: list[tuple[str, str, bytes]] = []

    def unary(self, service: str, method: str, payload: bytes):
        self.calls.append((service, method, payload))
        return _FakeResponse(self._body)


def _auth_with_body(body: bytes) -> BaleAuth:
    auth = BaleAuth.__new__(BaleAuth)
    auth._app_id = 4
    auth._api_key = "key"
    auth._device_title = "userbot-bale-ci"
    auth._device_hash = b"device"
    auth._client = _FakeClient(body)
    auth._last_tx = None
    auth._last_start_meta = None
    auth._last_send_code_type = 0
    return auth


def test_start_phone_auth_baleonly_success():
    body = _encode_start_response(
        sent_code_type=SEND_CODE_TYPE_BALEONLY,
        available=(SEND_CODE_TYPE_BALEONLY, SEND_CODE_TYPE_SMS),
    )
    auth = _auth_with_body(body)
    tx = auth.start_phone_auth(
        989120000000,
        send_code_type=SEND_CODE_TYPE_BALEONLY,
    )
    assert tx
    assert auth.last_start_meta is not None
    assert auth.last_start_meta.sent_code_type == SEND_CODE_TYPE_BALEONLY
    # request carried send_code_type=2
    _, _, payload = auth._client.calls[0]
    assert bytes.fromhex("4802") in payload


def test_start_phone_auth_baleonly_rejects_sms_fallback():
    body = _encode_start_response(
        sent_code_type=SEND_CODE_TYPE_SMS,
        available=(SEND_CODE_TYPE_SMS, SEND_CODE_TYPE_DEFAULT),
    )
    auth = _auth_with_body(body)
    with pytest.raises(BaleCodeChannelUnavailable) as excinfo:
        auth.start_phone_auth(
            989120000000,
            send_code_type=SEND_CODE_TYPE_BALEONLY,
        )
    assert excinfo.value.sent_code_type == SEND_CODE_TYPE_SMS
    assert SEND_CODE_TYPE_BALEONLY not in excinfo.value.available
    # no transaction retained on failure
    assert auth._last_tx is None


def test_start_phone_auth_baleonly_rejects_missing_metadata():
    body = _encode_start_response(
        sent_code_type=0,
        available=(),
    )
    auth = _auth_with_body(body)
    with pytest.raises(BaleCodeChannelUnavailable):
        auth.start_phone_auth(
            989120000000,
            send_code_type=SEND_CODE_TYPE_BALEONLY,
        )


def test_start_phone_auth_baleonly_rejects_when_not_in_available_list():
    # sent_code_type claims baleonly but the availability list contradicts it
    body = _encode_start_response(
        sent_code_type=SEND_CODE_TYPE_BALEONLY,
        available=(SEND_CODE_TYPE_SMS,),
    )
    auth = _auth_with_body(body)
    with pytest.raises(BaleCodeChannelUnavailable):
        auth.start_phone_auth(
            989120000000,
            send_code_type=SEND_CODE_TYPE_BALEONLY,
        )


def test_start_phone_auth_default_channel_skips_validation():
    body = _encode_start_response(
        sent_code_type=SEND_CODE_TYPE_SMS,
        available=(SEND_CODE_TYPE_SMS,),
    )
    auth = _auth_with_body(body)
    tx = auth.start_phone_auth(989120000000)
    assert tx
    assert auth._last_send_code_type == 0
