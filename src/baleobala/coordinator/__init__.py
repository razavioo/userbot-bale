"""Coordinator: central rendezvous + worker registry for Bale VPN.

The coordinator holds the authoritative view of which relay slots are free,
hands out assignments to incoming clients, and instructs relays to expect a
client call. All transport happens over short Bale calls + LiveKit data
channel — no out-of-band HTTP.

See plan: /Users/emad/.claude/plans/hidden-dreaming-fairy.md
"""

from baleobala.coordinator.protocol import (
    CONTROL_TOPIC,
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
from baleobala.coordinator.registry import RelayRegistry, RelaySlot, Session
from baleobala.coordinator.policy import pick_relay
from baleobala.coordinator.service import CoordinatorService, ServiceConfig
from baleobala.coordinator.transport import CoordinatorTransport, IncomingCall

__all__ = [
    "CONTROL_TOPIC",
    "ControlMessage",
    "CoordinatorService",
    "CoordinatorTransport",
    "DenyReason",
    "IncomingCall",
    "Kind",
    "RelayRegistry",
    "RelaySlot",
    "ServiceConfig",
    "Session",
    "decode",
    "encode",
    "make_assign",
    "make_deny",
    "make_expect_client",
    "make_heartbeat",
    "make_hello",
    "make_offline",
    "make_online",
    "make_released",
    "pick_relay",
]
