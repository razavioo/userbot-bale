from __future__ import annotations

from baleobala.control.scenario import build_proxy_pair_scenario, build_tunnel_pair_scenario


def test_build_proxy_pair_scenario_uses_real_cli_commands() -> None:
    scenario = build_proxy_pair_scenario(
        server_jwt_file="/tmp/server.jwt",
        client_jwt_file="/tmp/client.jwt",
        peer_id=42,
        proxy_secret="secret",
    )
    assert scenario.kind == "proxy-pair"
    assert "bale-proxy" in scenario.server_cmd
    assert "relay" in scenario.server_cmd
    assert "client" in scenario.client_cmd
    assert scenario.smoke_client_cmd


def test_build_tunnel_pair_scenario_uses_real_cli_commands() -> None:
    scenario = build_tunnel_pair_scenario(
        server_jwt_file="/tmp/server.jwt",
        client_jwt_file="/tmp/client.jwt",
        peer_id=42,
        psk_file="/tmp/psk.txt",
    )
    assert scenario.kind == "tunnel-pair"
    assert "tunnel" in scenario.server_cmd
    assert "exit-node" in scenario.server_cmd
    assert "up" in scenario.client_cmd
    assert "--psk-file" in scenario.server_cmd
