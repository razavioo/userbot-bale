from __future__ import annotations

import subprocess
from dataclasses import dataclass


def test_app_control_status_has_native_app_shape(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("USERBOT_BALE_HOME", str(tmp_path))
    monkeypatch.setenv("USERBOT_BALE_SECRET_BACKEND", "file")

    from userbot_bale.control.app_control import AppControlBridge

    result = AppControlBridge().handle({"command": "status", "payload": {}})

    assert result.ok is True
    payload = result.data
    assert payload["schemaVersion"] == 1
    assert payload["connectionState"] in {"signedOut", "firstRun", "blocked", "disconnected"}
    assert "networkPolicy" in payload
    assert "codeSigning" in payload
    assert any(item["key"] == "profile" for item in payload["readiness"])


def test_app_control_saves_network_policy_and_feeds_packet_tunnel(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("USERBOT_BALE_HOME", str(tmp_path))
    monkeypatch.setenv("USERBOT_BALE_SECRET_BACKEND", "file")

    from userbot_bale.control.app_control import AppControlBridge, load_network_policy
    from userbot_bale.control.macos import packet_tunnel_configuration
    from userbot_bale.control.vpn import VpnProfile

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

    payload = packet_tunnel_configuration(VpnProfile(profile_id="p1", name="userbot-bale"))
    assert payload["tunnelIPv4Address"] == "10.77.0.2"
    assert payload["includedIPv4Routes"] == ["0.0.0.0/0"]
    assert payload["includedIPv6Routes"] == []
    assert payload["killSwitchMode"] == "lockdown"
    assert payload["autoConnect"] is True
    assert payload["allowLAN"] is False
    assert payload["dnsServers"] == ["10.10.10.10"]
    assert payload["transportPreference"] == "dc"
    assert payload["fallbackProxyEnabled"] is False


def test_app_control_rejects_invalid_or_unavailable_network_policy(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("USERBOT_BALE_HOME", str(tmp_path))
    monkeypatch.setenv("USERBOT_BALE_SECRET_BACKEND", "file")

    from userbot_bale.control.app_control import AppControlBridge

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
    monkeypatch.setenv("USERBOT_BALE_HOME", str(tmp_path))
    monkeypatch.setenv("USERBOT_BALE_SECRET_BACKEND", "file")

    from userbot_bale.control.app_control import AppControlBridge

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
    monkeypatch.setenv("USERBOT_BALE_HOME", str(tmp_path))
    monkeypatch.setenv("USERBOT_BALE_SECRET_BACKEND", "file")

    from userbot_bale.control.app_control import AppControlBridge, auth_flow_path

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


def test_app_control_diagnostics_exports_redacted_log_tails(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("USERBOT_BALE_HOME", str(tmp_path))
    monkeypatch.setenv("USERBOT_BALE_SECRET_BACKEND", "file")

    from userbot_bale.control.app_control import AppControlBridge

    log_dir = tmp_path / "state"
    log_dir.mkdir()
    log_dir.joinpath("client.log").write_text(
        "connected\njwt=eyJhbGciOiJIUzI1NiJ9.secret.payload\nBearer abc.def.ghi\n",
        encoding="utf-8",
    )

    result = AppControlBridge().handle({"command": "diagnostics", "payload": {}})

    assert result.ok is True
    assert result.data["carrierSocketExists"] is False
    assert result.data["carrierSocketReachable"] is False
    assert result.data["carrierSocketState"] == "missing"
    tails = "\n".join(item["tail"] for item in result.data["redactedLogs"])
    assert "client.log" in {item["path"] for item in result.data["redactedLogs"]}
    assert "eyJhbGci" not in tails
    assert "abc.def.ghi" not in tails
    assert "<redacted" in tails
    assert "codeSigning" in result.data


def test_macos_code_signing_status_parses_security_identities(monkeypatch) -> None:
    from userbot_bale.control import macos

    monkeypatch.setattr(macos.sys, "platform", "darwin")

    def no_identities(cmd, *, check, capture_output, text):  # noqa: ANN001
        return subprocess.CompletedProcess(cmd, 0, stdout="     0 valid identities found\n", stderr="")

    missing = macos.code_signing_status(runner=no_identities)
    assert missing.state == "missing-identity"
    assert missing.valid_identities == 0

    def one_identity(cmd, *, check, capture_output, text):  # noqa: ANN001
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout='  1) ABCDEF1234567890 "Apple Development: Example (TEAMID)"\n     1 valid identities found\n',
            stderr="",
        )

    ready = macos.code_signing_status(runner=one_identity)
    assert ready.state == "ready"
    assert ready.valid_identities == 1


def test_cleanup_stale_carrier_socket_only_removes_unreachable_socket(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("USERBOT_BALE_HOME", str(tmp_path))

    from userbot_bale.control import macos

    socket_path = macos.carrier_socket_path({"carrierSocketPath": "carrier.sock"})
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.write_text("", encoding="utf-8")

    assert macos.cleanup_stale_carrier_socket({"carrierSocketPath": "carrier.sock"}) is True
    assert not socket_path.exists()
