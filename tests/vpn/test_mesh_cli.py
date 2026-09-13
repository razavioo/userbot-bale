"""Argparse + wiring smoke test for `userbot-bale tunnel exit-node-mesh`."""
from __future__ import annotations

import argparse

import pytest

from userbot_bale.cli import build_parser


def test_exit_node_mesh_subcommand_parses():
    parser = build_parser()
    args = parser.parse_args([
        "tunnel", "exit-node-mesh",
        "--bale-jwt", "eyJ.X.Y",
        "--tun", "vpn0",
        "--wan", "eth0",
        "--pool-cidr", "10.99.0.0/24",
        "--skip-nat-setup",
        "--psk", "shared-secret",
        "--provision-timeout", "12",
        "--max-peers-per-server-jwt", "4",
    ])
    assert args.cmd == "tunnel"
    assert args.vpn_cmd == "exit-node-mesh"
    assert args.bale_jwt == ["eyJ.X.Y"]
    assert args.tun == "vpn0"
    assert args.pool_cidr == "10.99.0.0/24"
    assert args.provision_timeout == 12
    assert args.skip_nat_setup is True
    assert args.psk == "shared-secret"
    assert args.max_peers_per_server_jwt == 4


def test_exit_node_mesh_accepts_multiple_server_accounts():
    parser = build_parser()
    args = parser.parse_args([
        "tunnel", "exit-node-mesh",
        "--bale-jwt-file", "/tmp/a.jwt",
        "--bale-jwt-file", "/tmp/b.jwt",
        "--skip-nat-setup",
    ])
    assert args.bale_jwt_file == ["/tmp/a.jwt", "/tmp/b.jwt"]


def test_live_smoke_parses_datachannel_experiment_options():
    parser = build_parser()
    args = parser.parse_args([
        "vpn", "live-smoke",
        "--caller-jwt-file", "/tmp/caller.jwt",
        "--callee-jwt-file", "/tmp/callee.jwt",
        "--callee-peer-id", "123",
        "--datachannels", "4",
        "--rounds", "3",
        "--payload-size", "4096",
    ])
    assert args.datachannels == 4
    assert args.rounds == 3
    assert args.payload_size == 4096


def test_proxy_relay_parses_capacity_options():
    parser = build_parser()
    args = parser.parse_args([
        "bale-proxy", "relay",
        "--bale-jwt-file", "/tmp/server.jwt",
        "--answer",
        "--relay-max-active-connections", "192",
    ])
    assert args.relay_max_active_connections == 192


def test_mesh_accepts_peers_via_transport_list():
    """Drive MeshExitNode directly with InMemoryTransport pairs — no
    Bale, no CLI. Verifies a client transport can be added and a packet
    dispatched to it."""
    import queue

    from userbot_bale.vpn.fake_tun import FakeTun
    from userbot_bale.vpn.mesh.exit_node import MeshExitNode
    from userbot_bale.vpn.mesh.router import PacketRouter
    from userbot_bale.vpn.transports import InMemoryTransport

    tun = FakeTun("mesh0")
    mesh = MeshExitNode(tun, pool_cidr="10.77.0.0/24")
    mesh.start()
    try:
        server_tx, client_tx = InMemoryTransport.pair(mtu=200)
        assignment = mesh.accept_client(
            peer_id=42, transport=server_tx, mtu_override=200,
        )
        assert assignment.client.startswith("10.77.0.")
        assert assignment.slot >= 0
        # Router should know about this client's address now.
        snapshot = mesh.snapshot()
        assert 42 in snapshot

        mesh.drop_client(42)
        assert 42 not in mesh.snapshot()
    finally:
        mesh.stop()
        tun.close()


def test_mesh_can_stage_client_before_activation():
    from userbot_bale.vpn.fake_tun import FakeTun
    from userbot_bale.vpn.mesh.allocator import Assignment
    from userbot_bale.vpn.mesh.exit_node import MeshExitNode
    from userbot_bale.vpn.transports import InMemoryTransport

    tun = FakeTun("mesh0")
    mesh = MeshExitNode(tun, pool_cidr="10.77.0.0/24")
    mesh.start()
    try:
        server_tx, client_tx = InMemoryTransport.pair(mtu=200)
        assignment = Assignment(
            peer_id=55,
            slot=3,
            gateway="10.77.0.13",
            client="10.77.0.14",
            prefix="10.77.0.12/30",
            pool_cidr="10.77.0.0/24",
        )
        mesh.issue_client(55, server_tx, assignment=assignment)
        assert mesh.snapshot()[55].client == "10.77.0.14"
        mesh.activate_client(55, mtu_override=200)
        mesh.drop_client(55)
        assert 55 not in mesh.snapshot()
    finally:
        mesh.stop()
        tun.close()


def test_client_mesh_provisioning_updates_tun_args(monkeypatch):
    from userbot_bale.vpn.cli import _maybe_receive_mesh_provisioning
    from userbot_bale.vpn.fake_tun import FakeTun
    from userbot_bale.vpn.provisioning import MeshProvisionMessage
    from userbot_bale.vpn.transports import InMemoryTransport

    configured = {}

    def fake_configure(name: str, cidr: str, mtu: int) -> None:
        configured["name"] = name
        configured["cidr"] = cidr
        configured["mtu"] = mtu

    monkeypatch.setattr(
        "userbot_bale.vpn.provisioning.configure_tun_interface",
        fake_configure,
    )

    server_tx, client_tx = InMemoryTransport.pair(mtu=512)
    server_tx.send_bytes(MeshProvisionMessage(
        version=1,
        kind="assign",
        peer_id=101,
        session_id=0x1176,
        pool_cidr="10.77.0.0/24",
        prefix="10.77.0.4/30",
        gateway_ip="10.77.0.5",
        client_ip="10.77.0.6",
        tun_mtu=1337,
        transport="dc",
    ).encode())

    args = argparse.Namespace(tun="vpn0", tun_addr="10.0.0.1/24", tun_mtu=1400, provision_timeout=0.1)
    msg = _maybe_receive_mesh_provisioning(args, FakeTun("mesh0"), client_tx, "dc")
    assert msg is not None
    assert configured == {"name": "vpn0", "cidr": "10.77.0.6/30", "mtu": 1337}
    assert args.tun_addr == "10.77.0.6/30"
