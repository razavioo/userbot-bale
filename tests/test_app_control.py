from __future__ import annotations

from dataclasses import dataclass


def test_app_control_status_has_native_app_shape(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    monkeypatch.setenv("BALEOBALA_SECRET_BACKEND", "file")

    from baleobala.control.app_control import AppControlBridge

    result = AppControlBridge().handle({"command": "status", "payload": {}})

    assert result.ok is True
    payload = result.data
    assert payload["schemaVersion"] == 1
    assert payload["connectionState"] in {"signedOut", "firstRun", "blocked", "disconnected"}
    assert "networkPolicy" in payload
    assert any(item["key"] == "profile" for item in payload["readiness"])


def test_app_control_saves_network_policy_and_feeds_packet_tunnel(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    monkeypatch.setenv("BALEOBALA_SECRET_BACKEND", "file")

    from baleobala.control.app_control import AppControlBridge, load_network_policy
    from baleobala.control.macos import packet_tunnel_configuration
    from baleobala.control.vpn import VpnProfile

    bridge = AppControlBridge()
    result = bridge.handle(
        {
            "command": "setNetworkPolicy",
            "payload": {
                "kill_switch_mode": "lockdown",
                "auto_connect": True,
                "allow_lan": False,
                "dns_mode": "custom",
                "custom_dns_servers": ["10.10.10.10"],
                "transport_preference": "dc",
            },
        }
    )

    assert result.ok is True
    policy = load_network_policy()
    assert policy.kill_switch_mode == "lockdown"
    assert policy.auto_connect is True
    assert policy.allow_lan is False
    assert policy.custom_dns_servers == ["10.10.10.10"]

    payload = packet_tunnel_configuration(VpnProfile(profile_id="p1", name="baleobala"))
    assert payload["killSwitchMode"] == "lockdown"
    assert payload["autoConnect"] is True
    assert payload["allowLAN"] is False
    assert payload["dnsServers"] == ["10.10.10.10"]
    assert payload["transportPreference"] == "dc"
    assert payload["fallbackProxyEnabled"] is False


def test_app_control_rejects_invalid_or_unavailable_network_policy(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    monkeypatch.setenv("BALEOBALA_SECRET_BACKEND", "file")

    from baleobala.control.app_control import AppControlBridge

    bridge = AppControlBridge()
    invalid_ip = bridge.handle(
        {
            "command": "setNetworkPolicy",
            "payload": {"dns_mode": "custom", "custom_dns_servers": ["not-an-ip"]},
        }
    )
    unavailable = bridge.handle(
        {
            "command": "setNetworkPolicy",
            "payload": {"dns_mode": "block_ads_trackers", "custom_dns_servers": ["1.1.1.1"]},
        }
    )
    helper_required = bridge.handle(
        {
            "command": "setNetworkPolicy",
            "payload": {"fallback_proxy_enabled": True},
        }
    )

    assert invalid_ip.ok is False
    assert "valid DNS server" in invalid_ip.message
    assert unavailable.ok is False
    assert "blocklist" in unavailable.message
    assert helper_required.ok is False
    assert "signed helper" in helper_required.message


def test_app_control_pair_command_creates_relay_summary(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    monkeypatch.setenv("BALEOBALA_SECRET_BACKEND", "file")

    from baleobala.control.app_control import AppControlBridge

    bridge = AppControlBridge()
    result = bridge.handle(
        {
            "command": "pair",
            "payload": {
                "name": "home-relay",
                "role": "client",
                "backend": "packet-tunnel",
                "transport_preference": "dc",
            },
        }
    )

    assert result.ok is True
    status = bridge.handle({"command": "status"}).data
    assert status["relays"]
    assert status["relays"][0]["name"] == "home-relay"
    assert status["relays"][0]["transportPreference"] == "dc"
    assert status["vpn"]["backend"] == "packet-tunnel"


def test_app_control_auth_flow_survives_one_shot_helper_processes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    monkeypatch.setenv("BALEOBALA_SECRET_BACKEND", "file")

    from baleobala.control.app_control import AppControlBridge, auth_flow_path

    @dataclass(frozen=True)
    class FakeSession:
        jwt: str
        response_body: bytes = b""

    class FakeAuth:
        def start_phone_auth(self, phone: int) -> str:
            assert phone == 989123456789
            return "tx-123"

        def validate_code(self, code: str, *, transaction_hash: str | None = None) -> FakeSession:
            assert code == "12345"
            assert transaction_hash == "tx-123"
            return FakeSession(jwt="jwt-token")

    start = AppControlBridge(auth_factory=FakeAuth).handle(
        {"command": "startAuth", "payload": {"phone": "+989123456789"}}
    )
    verify = AppControlBridge(auth_factory=FakeAuth).handle(
        {"command": "verifyAuth", "payload": {"code": "12345"}}
    )

    assert start.ok is True
    assert start.data["transactionHash"] == "tx-123"
    assert verify.ok is True
    assert verify.data["auth"]["provider"] == "bale"
    assert verify.data["auth"]["phone"] == "+989123456789"
    assert not auth_flow_path().exists()
