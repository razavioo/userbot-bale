from __future__ import annotations

import pytest

from baleobala.coordinator.protocol import (
    CONTROL_TOPIC,
    ControlError,
    ControlMessage,
    DenyReason,
    Kind,
    decode,
    encode,
    make_assign,
    make_deny,
    make_expect_client,
    make_heartbeat,
    make_hello,
    make_offline,
    make_online,
    make_released,
)


def test_control_topic_name():
    assert CONTROL_TOPIC == "control"


@pytest.mark.parametrize(
    "factory,kwargs,expected_keys",
    [
        (make_hello, {"client_id": "abc", "app_version": "1.2.3"}, {"client_id", "app_version"}),
        (make_assign, {"relay_peer_id": 7, "session_id": "s", "expires_in_secs": 30},
         {"relay_peer_id", "session_id", "expires_in_secs"}),
        (make_deny, {"reason": DenyReason.NO_CAPACITY}, {"reason", "detail"}),
        (make_expect_client, {"client_peer_id": 9, "session_id": "s", "expires_in_secs": 10},
         {"client_peer_id", "session_id", "expires_in_secs"}),
        (make_online, {"relay_id": "r1", "peer_id": 9, "capacity": 2},
         {"relay_id", "peer_id", "capacity"}),
        (make_heartbeat, {"relay_id": "r1", "in_use": [1, 2]}, {"relay_id", "in_use"}),
        (make_released, {"relay_id": "r1", "session_id": "s"}, {"relay_id", "session_id"}),
        (make_offline, {"relay_id": "r1", "reason": "shutdown"}, {"relay_id", "reason"}),
    ],
)
def test_factories_produce_expected_body(factory, kwargs, expected_keys):
    msg = factory(**kwargs)
    assert isinstance(msg, ControlMessage)
    assert set(msg.body.keys()) == expected_keys


def test_round_trip_preserves_kind_and_body():
    msg = make_assign(relay_peer_id=42, session_id="abcd", expires_in_secs=30)
    encoded = encode(msg)
    assert encoded.startswith(b"BBCOORD1:")
    out = decode(encoded)
    assert out.kind == Kind.ASSIGN
    assert out.get("relay_peer_id") == 42
    assert out.get("session_id") == "abcd"
    assert out.get("expires_in_secs") == 30


def test_round_trip_handles_lists():
    msg = make_heartbeat(relay_id="r1", in_use=[10, 20, 30])
    out = decode(encode(msg))
    assert out.get("in_use") == [10, 20, 30]


def test_decode_rejects_payload_without_magic():
    with pytest.raises(ControlError):
        decode(b'{"kind": "HELLO"}')


def test_decode_rejects_invalid_json():
    with pytest.raises(ControlError):
        decode(b"BBCOORD1:not-json")


def test_decode_rejects_non_object_payload():
    with pytest.raises(ControlError):
        decode(b'BBCOORD1:[]')


def test_decode_rejects_missing_kind():
    with pytest.raises(ControlError):
        decode(b'BBCOORD1:{"v":1}')


def test_int_coercion_in_factories():
    msg = make_assign(relay_peer_id="7", session_id="x", expires_in_secs="10")  # type: ignore[arg-type]
    assert msg.get("relay_peer_id") == 7
    assert msg.get("expires_in_secs") == 10
