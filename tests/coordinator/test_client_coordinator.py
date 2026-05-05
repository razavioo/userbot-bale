"""Tests for _resolve_via_coordinator — the client-side coordinator flow."""

from __future__ import annotations

import argparse
import queue
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from baleobala.coordinator.protocol import (
    DenyReason,
    Kind,
    ControlMessage,
    encode,
    make_assign,
    make_deny,
    CONTROL_TOPIC,
)
from baleobala.coordinator.client_flow import resolve_via_coordinator


# ---- Fakes ---------------------------------------------------------------

class FakeDataChannel:
    def __init__(self, reply: bytes | None) -> None:
        self._reply = reply
        self.sent: list[bytes] = []

    def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)

    def recv_bytes(self, timeout: float | None = None) -> bytes | None:
        return self._reply

    def close(self) -> None:
        pass


class FakeLKSession:
    def __init__(self, *, url, token, identity, reply_msg=None):
        self.url = url
        self.reply_msg = reply_msg
        self.started = False
        self.stopped = False
        self._channels: dict[str, FakeDataChannel] = {}

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def wait_for_remote_participant(self, timeout=15.0) -> None:
        pass

    def data_channel(self, *, topic="vpn", reliable=True) -> FakeDataChannel:
        if topic not in self._channels:
            payload = encode(self.reply_msg) if self.reply_msg else None
            self._channels[topic] = FakeDataChannel(payload)
        return self._channels[topic]


class FakeBaleApiClient:
    def __init__(self, *, jwt, ws_tls_config=None):
        self.jwt = jwt
        self.started = False
        self.stopped = False
        self._creds_seq: list[Any] = []

    def start(self, *a, **kw) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def fetch_livekit_credentials(self, peer_id, *, creds_timeout=30.0):
        from baleobala.bale.protos import CallCredentials
        return CallCredentials(url=f"wss://lk/{peer_id}", token=f"tok-{peer_id}",
                               room=f"room-{peer_id}", peer_id=peer_id)


class FakeBaleCarrierController:
    def __init__(self, *, client):
        self.client = client
        self.answered_timeout: float | None = None

    def answer(self, timeout=60.0):
        self.answered_timeout = timeout
        from baleobala.carrier.bale import CarrierCredentials
        return CarrierCredentials(url="wss://lk/relay", token="tok-relay",
                                  room="room-relay", identity="client")


# ---- helpers to build fake sessions keyed by coordinator / relay ----------

def make_session_factory(assign_msg=None, deny_msg=None):
    """Return a LiveKitSession factory that injects control messages."""
    calls: list[FakeLKSession] = []
    reply = assign_msg or deny_msg

    def factory(*, url, token, identity):
        s = FakeLKSession(url=url, token=token, identity=identity, reply_msg=reply)
        calls.append(s)
        return s

    return factory, calls


# ---- tests ---------------------------------------------------------------

@patch("baleobala.coordinator.client_flow.BaleApiClient")
@patch("baleobala.coordinator.client_flow.BaleCarrierController")
@patch("baleobala.coordinator.client_flow.LiveKitSession")
def test_assign_flow_ends_in_answer_mode(MockLK, MockCtrl, MockAPI):
    assign = make_assign(relay_peer_id=200, session_id="s1", expires_in_secs=30)
    MockLK.return_value = FakeLKSession(
        url="wss://x", token="t", identity="c", reply_msg=assign
    )
    MockAPI.return_value = FakeBaleApiClient(jwt="jwt")
    ctrl_instance = FakeBaleCarrierController(client=MockAPI.return_value)
    MockCtrl.return_value = ctrl_instance

    creds = resolve_via_coordinator(
        coordinator_peer_id=9000,
        jwt="jwt",
        ws_tls_config=None,
        identity="test-client",
        answer_timeout=30.0,
    )

    assert ctrl_instance.answered_timeout == 40.0  # expires_in + 10
    assert creds.url == "wss://lk/relay"


@patch("baleobala.coordinator.client_flow.BaleApiClient")
@patch("baleobala.coordinator.client_flow.BaleCarrierController")
@patch("baleobala.coordinator.client_flow.LiveKitSession")
def test_deny_raises_system_exit(MockLK, MockCtrl, MockAPI):
    deny = make_deny(reason=DenyReason.NO_CAPACITY)
    MockLK.return_value = FakeLKSession(
        url="wss://x", token="t", identity="c", reply_msg=deny
    )
    MockAPI.return_value = FakeBaleApiClient(jwt="jwt")
    MockCtrl.return_value = FakeBaleCarrierController(client=MockAPI.return_value)

    with pytest.raises(SystemExit, match="no_capacity"):
        resolve_via_coordinator(
            coordinator_peer_id=9000,
            jwt="jwt",
            ws_tls_config=None,
            identity="test-client",
            answer_timeout=30.0,
        )


@patch("baleobala.coordinator.client_flow.BaleApiClient")
@patch("baleobala.coordinator.client_flow.BaleCarrierController")
@patch("baleobala.coordinator.client_flow.LiveKitSession")
def test_no_coordinator_response_raises(MockLK, MockCtrl, MockAPI):
    MockLK.return_value = FakeLKSession(
        url="wss://x", token="t", identity="c", reply_msg=None  # no reply
    )
    MockAPI.return_value = FakeBaleApiClient(jwt="jwt")
    MockCtrl.return_value = FakeBaleCarrierController(client=MockAPI.return_value)

    with pytest.raises(SystemExit, match="did not respond"):
        resolve_via_coordinator(
            coordinator_peer_id=9000,
            jwt="jwt",
            ws_tls_config=None,
            identity="test-client",
            answer_timeout=30.0,
        )


@patch("baleobala.coordinator.client_flow.BaleApiClient")
@patch("baleobala.coordinator.client_flow.BaleCarrierController")
@patch("baleobala.coordinator.client_flow.LiveKitSession")
def test_hello_is_sent_with_client_id(MockLK, MockCtrl, MockAPI):
    from baleobala.coordinator.protocol import decode as ctrl_decode, Kind

    assign = make_assign(relay_peer_id=200, session_id="s1", expires_in_secs=10)
    session = FakeLKSession(url="wss://x", token="t", identity="c", reply_msg=assign)
    MockLK.return_value = session
    MockAPI.return_value = FakeBaleApiClient(jwt="jwt")
    MockCtrl.return_value = FakeBaleCarrierController(client=MockAPI.return_value)

    resolve_via_coordinator(
        coordinator_peer_id=9000,
        jwt="jwt",
        ws_tls_config=None,
        identity="my-client",
        answer_timeout=10.0,
    )

    ch = session._channels.get(CONTROL_TOPIC)
    assert ch is not None and ch.sent
    hello = ctrl_decode(ch.sent[0])
    assert hello.kind == Kind.HELLO
    assert hello.get("client_id") == "my-client"


def test_load_coordinator_peer_id_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("BALEOBALA_COORDINATOR_PEER_ID", "12345")
    from baleobala.control.coordinator_config import load_coordinator_peer_id
    assert load_coordinator_peer_id() == 12345


def test_load_coordinator_peer_id_from_file(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    monkeypatch.delenv("BALEOBALA_COORDINATOR_PEER_ID", raising=False)
    cfg = tmp_path / "coordinator.json"
    cfg.write_text(json.dumps({"coordinator_peer_id": 99999}))
    from importlib import reload
    import baleobala.control.coordinator_config as m
    reload(m)  # pick up new env
    assert m.load_coordinator_peer_id() == 99999


def test_load_coordinator_peer_id_returns_none_if_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    monkeypatch.delenv("BALEOBALA_COORDINATOR_PEER_ID", raising=False)
    from importlib import reload
    import baleobala.control.coordinator_config as m
    reload(m)
    assert m.load_coordinator_peer_id() is None
