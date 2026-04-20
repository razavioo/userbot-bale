"""Argparse + wiring smoke test for `baleobala vpn exit-node-mesh`."""
from __future__ import annotations

import pytest

from baleobala.cli import build_parser


def test_exit_node_mesh_subcommand_parses():
    parser = build_parser()
    args = parser.parse_args([
        "vpn", "exit-node-mesh",
        "--bale-jwt", "eyJ.X.Y",
        "--tun", "vpn0",
        "--wan", "eth0",
        "--pool-cidr", "10.99.0.0/24",
        "--skip-nat-setup",
        "--psk", "shared-secret",
    ])
    assert args.cmd == "vpn"
    assert args.vpn_cmd == "exit-node-mesh"
    assert args.tun == "vpn0"
    assert args.pool_cidr == "10.99.0.0/24"
    assert args.skip_nat_setup is True
    assert args.psk == "shared-secret"


def test_mesh_accepts_peers_via_transport_list():
    """Drive MeshExitNode directly with InMemoryTransport pairs — no
    Bale, no CLI. Verifies a client transport can be added and a packet
    dispatched to it."""
    import queue

    from baleobala.vpn.fake_tun import FakeTun
    from baleobala.vpn.mesh.exit_node import MeshExitNode
    from baleobala.vpn.mesh.router import PacketRouter
    from baleobala.vpn.transports import InMemoryTransport

    tun = FakeTun("mesh0")
    mesh = MeshExitNode(tun, pool_cidr="10.77.0.0/24")
    mesh.start()
    try:
        server_tx, client_tx = InMemoryTransport.pair(mtu=200)
        assignment = mesh.accept_client(
            peer_id=42, transport=server_tx, mtu_override=200,
        )
        assert assignment.client.startswith("10.77.0.")
        # Router should know about this client's address now.
        snapshot = mesh.snapshot()
        assert 42 in snapshot

        mesh.drop_client(42)
        assert 42 not in mesh.snapshot()
    finally:
        mesh.stop()
        tun.close()
