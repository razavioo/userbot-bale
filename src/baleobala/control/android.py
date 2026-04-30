"""Android VPN-service helpers for the shared control plane."""

from __future__ import annotations

import json
from pathlib import Path

from baleobala.control.paths import shared_container_dir
from baleobala.control.probe import probe_endpoint

_VPN_PROFILE_NAME = "android_vpn_profile.json"
_VPN_ACTIVATE_REQUEST = "android_vpn_activate.request.json"


def vpn_profile_path() -> Path:
    return shared_container_dir() / _VPN_PROFILE_NAME


def activation_request_path() -> Path:
    return shared_container_dir() / _VPN_ACTIVATE_REQUEST


def load_vpn_profile() -> dict[str, object] | None:
    path = vpn_profile_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def vpn_profile_installed(config: dict[str, object] | None = None) -> bool:
    payload = load_vpn_profile()
    if payload is None:
        return False
    if config is None:
        return True
    return payload == config


def vpn_service_configuration(profile, pairing=None) -> dict[str, object]:  # noqa: ANN001
    relay_identifier = ""
    if pairing is not None:
        relay_identifier = str(getattr(pairing, "relay_id", "") or "")
    if not relay_identifier:
        relay_identifier = str(getattr(profile, "pairing_id", "") or "")

    dns_servers = ["1.1.1.1", "9.9.9.9"]
    policy_payload: dict[str, object] = {
        "killSwitchMode": "off",
        "autoConnect": False,
        "allowLAN": True,
        "transportPreference": "auto",
    }
    try:
        from baleobala.control.app_control import load_network_policy

        policy = load_network_policy()
        policy_payload = {
            "killSwitchMode": policy.kill_switch_mode,
            "autoConnect": policy.auto_connect,
            "allowLAN": policy.allow_lan,
            "transportPreference": policy.transport_preference,
        }
        if policy.dns_mode != "system":
            dns_servers = list(policy.custom_dns_servers)
        else:
            dns_servers = []
    except Exception:  # noqa: BLE001
        pass

    return {
        "configurationVersion": 1,
        "displayName": getattr(profile, "name", "baleobala"),
        "tunnelIPv4Address": "10.77.0.2",
        "tunnelIPv4PrefixLength": 24,
        "includedIPv4Routes": ["0.0.0.0/0"],
        "includedIPv6Routes": [],
        "excludedRoutes": ["127.0.0.0/8"],
        "dnsServers": dns_servers,
        "searchDomains": [],
        "mtu": 1400,
        "overheadBytes": 80,
        "carrierSocketPath": "carrier_tunnel.sock",
        "relayIdentifier": relay_identifier,
        "vpnServiceHost": getattr(profile, "listen_host", "127.0.0.1"),
        "vpnServicePort": getattr(profile, "listen_port", 1080),
        **policy_payload,
    }


def carrier_socket_path(config: dict[str, object] | None = None) -> Path:
    payload = config or load_vpn_profile() or {}
    return shared_container_dir() / str(payload.get("carrierSocketPath") or "carrier_tunnel.sock")


def cleanup_stale_carrier_socket(config: dict[str, object] | None = None, *, timeout: float = 0.2) -> bool:
    path = carrier_socket_path(config)
    if not path.exists():
        return False
    probe = probe_endpoint(f"unix://{path}", timeout=timeout)
    if probe.ok:
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


def install_vpn_profile(config: dict[str, object]) -> dict[str, str]:
    shared_container_dir().mkdir(parents=True, exist_ok=True)
    path = vpn_profile_path()
    payload = dict(config)
    payload.setdefault("configurationVersion", 1)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    request_path = activation_request_path()
    request_path.write_text(
        json.dumps(
            {
                "type": "install_android_vpn_profile",
                "profile_path": str(path),
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return {
        "state": "installed",
        "profile_path": str(path),
        "request_path": str(request_path),
    }
