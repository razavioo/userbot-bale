"""Coordinator control-plane message format.

Messages travel over LiveKit data channel topic "control" inside short Bale
calls. They are framed with a magic prefix so a peer that mis-routes them to
the VPN topic does not interpret arbitrary bytes as control traffic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Final


CONTROL_TOPIC: Final[str] = "control"
MAGIC: Final[bytes] = b"BBCOORD1:"
PROTOCOL_VERSION: Final[int] = 1


class Kind:
    HELLO = "HELLO"
    ASSIGN = "ASSIGN"
    DENY = "DENY"
    EXPECT_CLIENT = "EXPECT_CLIENT"
    EXPECT_ACK = "EXPECT_ACK"
    ONLINE = "ONLINE"
    HEARTBEAT = "HEARTBEAT"
    RELEASED = "RELEASED"
    OFFLINE = "OFFLINE"


class DenyReason:
    NO_CAPACITY = "no_capacity"
    BLOCKED = "blocked"
    CLIENT_UNKNOWN = "client_unknown"
    INTERNAL = "internal"


class ControlError(RuntimeError):
    """Raised when a payload cannot be parsed as a coordinator message."""


@dataclass(frozen=True)
class ControlMessage:
    kind: str
    body: dict[str, Any] = field(default_factory=dict)
    version: int = PROTOCOL_VERSION

    def get(self, key: str, default: Any = None) -> Any:
        return self.body.get(key, default)


def encode(msg: ControlMessage) -> bytes:
    payload = {"v": msg.version, "kind": msg.kind, **msg.body}
    return MAGIC + json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def decode(payload: bytes) -> ControlMessage:
    if not payload.startswith(MAGIC):
        raise ControlError("not a coordinator control message")
    try:
        data = json.loads(payload[len(MAGIC):].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControlError("invalid control payload") from exc
    if not isinstance(data, dict):
        raise ControlError("control payload must be a JSON object")
    kind = data.pop("kind", None)
    version = int(data.pop("v", PROTOCOL_VERSION))
    if not isinstance(kind, str) or not kind:
        raise ControlError("control payload missing 'kind'")
    return ControlMessage(kind=kind, body=data, version=version)


def make_hello(
    *,
    client_id: str,
    app_version: str = "",
    client_peer_id: int | None = None,
) -> ControlMessage:
    body: dict[str, Any] = {"client_id": client_id, "app_version": app_version}
    if client_peer_id is not None:
        body["client_peer_id"] = int(client_peer_id)
    return ControlMessage(kind=Kind.HELLO, body=body)


def make_assign(*, relay_peer_id: int, session_id: str, expires_in_secs: int) -> ControlMessage:
    return ControlMessage(
        kind=Kind.ASSIGN,
        body={
            "relay_peer_id": int(relay_peer_id),
            "session_id": str(session_id),
            "expires_in_secs": int(expires_in_secs),
        },
    )


def make_deny(*, reason: str, detail: str = "") -> ControlMessage:
    return ControlMessage(kind=Kind.DENY, body={"reason": reason, "detail": detail})


def make_expect_client(*, client_peer_id: int, session_id: str, expires_in_secs: int) -> ControlMessage:
    return ControlMessage(
        kind=Kind.EXPECT_CLIENT,
        body={
            "client_peer_id": int(client_peer_id),
            "session_id": str(session_id),
            "expires_in_secs": int(expires_in_secs),
        },
    )


def make_online(*, relay_id: str, peer_id: int, capacity: int = 1) -> ControlMessage:
    return ControlMessage(
        kind=Kind.ONLINE,
        body={"relay_id": relay_id, "peer_id": int(peer_id), "capacity": int(capacity)},
    )


def make_heartbeat(
    *,
    relay_id: str,
    in_use: list[int],
    peer_id: int | None = None,
    capacity: int | None = None,
) -> ControlMessage:
    body: dict[str, Any] = {
        "relay_id": relay_id,
        "in_use": [int(p) for p in in_use],
    }
    if peer_id is not None:
        body["peer_id"] = int(peer_id)
    if capacity is not None:
        body["capacity"] = int(capacity)
    return ControlMessage(kind=Kind.HEARTBEAT, body=body)


def make_released(*, relay_id: str, session_id: str) -> ControlMessage:
    return ControlMessage(
        kind=Kind.RELEASED,
        body={"relay_id": relay_id, "session_id": session_id},
    )


def make_offline(*, relay_id: str, reason: str = "") -> ControlMessage:
    return ControlMessage(kind=Kind.OFFLINE, body={"relay_id": relay_id, "reason": reason})
