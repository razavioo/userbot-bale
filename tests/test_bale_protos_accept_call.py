from __future__ import annotations

from baleobala.bale.protos import encode_accept_call, parse_incoming_call_offer


def test_encode_accept_call_is_unwrapped() -> None:
    payload = encode_accept_call(123, invite_enable=True)
    assert payload.hex() == "087b12020801"


def test_encode_accept_call_without_invite_flag() -> None:
    payload = encode_accept_call(123, invite_enable=False)
    assert payload.hex() == "087b"


def test_parse_incoming_call_offer_extracts_call_id() -> None:
    raw = bytes.fromhex(
        "12770a750a68bae419640a6208ac9a85f2add5c385181a243138306230656161"
        "2d646534312d346432632d613235362d65376138373938323530623722170a15"
        "7773733a2f2f6d6565742d6777652e626c652e697230f084cdb9db3340bb91bf"
        "d4054a08080110c491e7c901600118ea940120f684cdb9db33"
    )
    assert parse_incoming_call_offer(raw) is not None
