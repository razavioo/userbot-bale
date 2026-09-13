from __future__ import annotations

from userbot_bale.control.scenario import build_proxy_pair_scenario, build_tunnel_pair_scenario


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
    assert scenario.smoke_server_cmd
    assert scenario.smoke_client_cmd
    assert scenario.smoke_kind == "tcp-connect-echo"
    assert "call_established" in scenario.required_markers
    assert "command_transcript.json" in scenario.bundle_expectations


def test_build_tunnel_pair_scenario_uses_real_cli_commands() -> None:
    scenario = build_tunnel_pair_scenario(
        server_jwt_file="/tmp/server.jwt",
        client_jwt_file="/tmp/client.jwt",
        peer_id=42,
        psk_file="/tmp/psk.txt",
        full_device=True,
    )
    assert scenario.kind == "tunnel-pair"
    assert "tunnel" in scenario.server_cmd
    assert "exit-node" in scenario.server_cmd
    assert "up" in scenario.client_cmd
    assert "--psk-file" in scenario.server_cmd
    assert scenario.network_plan["full_device"] is True
    assert scenario.network_plan["server"]["enable_nat"] is True
    assert scenario.network_plan["setup_host_routes"] is True
    assert scenario.network_plan["dns_probe_host"] == "example.com"
    assert scenario.network_plan["egress_probe_host"] == "1.1.1.1"
    assert scenario.smoke_client_cmd
    assert "route" in scenario.full_device_checks
    assert "call_established" in scenario.required_markers


def test_build_tunnel_pair_scenario_can_disable_full_device_automation() -> None:
    scenario = build_tunnel_pair_scenario(
        server_jwt_file="/tmp/server.jwt",
        client_jwt_file="/tmp/client.jwt",
        peer_id=42,
        full_device=False,
        skip_nat_setup=True,
        skip_host_route_setup=True,
    )
    assert scenario.network_plan["full_device"] is False
    assert scenario.network_plan["client"]["routes"] == []
    assert scenario.network_plan["server"]["enable_nat"] is False
    assert scenario.network_plan["setup_host_routes"] is False
