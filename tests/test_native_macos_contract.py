from __future__ import annotations

import plistlib
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _match(pattern: str, text: str) -> str:
    found = re.search(pattern, text, re.M)
    assert found is not None
    return found.group(1)


def _xcconfig_value(path: str, key: str) -> str:
    text = _read(path)
    return _match(rf"^{re.escape(key)}\s*=\s*(\S+)\s*$", text)


def test_macos_app_group_is_consistent() -> None:
    swift = _read("native/macos/Shared/BaleAppGroup.swift")
    app_group = _xcconfig_value("native/macos/Config/Base.xcconfig", "BALEOBALA_APP_GROUP_IDENTIFIER")

    app_entitlements = plistlib.loads((REPO_ROOT / "native/macos/BaleobalaApp/BaleobalaApp.entitlements").read_bytes())
    tunnel_entitlements = plistlib.loads((REPO_ROOT / "native/macos/BaleobalaPacketTunnel/BaleobalaPacketTunnel.entitlements").read_bytes())

    assert "BaleAppGroupIdentifier" in _read("native/macos/BaleobalaApp/Info.plist")
    assert "BaleAppGroupIdentifier" in _read("native/macos/BaleobalaPacketTunnel/Info.plist")
    assert "infoValue(\"BaleAppGroupIdentifier\"" in swift
    assert app_group == "group.com.baleobala.vpn"
    assert "$(BALEOBALA_APP_GROUP_IDENTIFIER)" in app_entitlements["com.apple.security.application-groups"]
    assert "$(BALEOBALA_APP_GROUP_IDENTIFIER)" in tunnel_entitlements["com.apple.security.application-groups"]


def test_packet_tunnel_bundle_identifier_is_consistent() -> None:
    swift = _read("native/macos/Shared/BaleAppGroup.swift")
    provider_bundle = _xcconfig_value("native/macos/Config/Base.xcconfig", "BALEOBALA_PACKET_TUNNEL_BUNDLE_ID")
    xcconfig = _read("native/macos/Config/PacketTunnel.xcconfig")
    info_plist = plistlib.loads((REPO_ROOT / "native/macos/BaleobalaPacketTunnel/Info.plist").read_bytes())
    tunnel_manager = _read("native/macos/Shared/BaleTunnelManager.swift")

    assert provider_bundle == "$(BALEOBALA_APP_BUNDLE_ID).packet-tunnel"
    assert "PRODUCT_BUNDLE_IDENTIFIER = $(BALEOBALA_PACKET_TUNNEL_BUNDLE_ID)" in xcconfig
    assert info_plist["CFBundleIdentifier"] == "$(PRODUCT_BUNDLE_IDENTIFIER)"
    assert "BaleProviderBundleIdentifier" in info_plist
    assert "infoValue(\"BaleProviderBundleIdentifier\"" in swift
    assert "first(where:" in tunnel_manager
    assert "providerBundleIdentifier == BaleAppGroup.providerBundleIdentifier" in tunnel_manager


def test_packet_tunnel_provider_implements_control_status_contract() -> None:
    provider = _read("native/macos/BaleobalaPacketTunnel/PacketTunnelProvider.swift")
    assert "handleControlMessage" in provider
    assert '"type": "status"' in provider
    assert '"type": "pong"' in provider
    assert "statusVersion" in provider
    assert "dnsMode" in provider
    assert "customDNSServers" in provider


def test_native_app_declares_product_shell_and_json_models() -> None:
    app = _read("native/macos/BaleobalaApp/BaleobalaApp.swift")
    content = _read("native/macos/BaleobalaApp/ContentView.swift")
    controller = _read("native/macos/BaleobalaApp/BaleAppController.swift")
    models = _read("native/macos/Shared/BaleAppModels.swift")
    client = _read("native/macos/BaleobalaApp/BaleAppControlClient.swift")
    helper = _read("native/macos/BaleobalaApp/Resources/baleobala-app-control")
    project = _read("native/macos/Baleobala.xcodeproj/project.pbxproj")
    scheme = _read("native/macos/Baleobala.xcodeproj/xcshareddata/xcschemes/Baleobala.xcscheme")
    gitignore = _read(".gitignore")

    assert "MenuBarExtra" in app
    for section in ("Overview", "Relays", "Privacy", "Settings", "Diagnostics"):
        assert section in content
    assert "Export Diagnostics" in content
    assert "exportDiagnostics" in controller
    assert "NSSavePanel" in controller
    for model in ("BaleAppState", "ConnectionState", "ReadinessGate", "BaleNetworkPolicy", "DiagnosticSnapshot"):
        assert model in models
    assert "baleobala.control.app_control" in client
    assert "baleobala.control.app_control" in helper
    assert "BaleAppModels.swift in Sources" in project
    assert "BaleAppControlClient.swift in Sources" in project
    assert "baleobala-app-control in Resources" in project
    assert "baleobala in Resources" in project
    assert "BlueprintName = \"Baleobala\"" in scheme
    assert "/native/macos/Baleobala.xcodeproj/" not in gitignore


def test_tunnel_configuration_carries_network_policy() -> None:
    config = _read("native/macos/Shared/BaleTunnelConfiguration.swift")
    manager = _read("native/macos/Shared/BaleTunnelManager.swift")

    for field in (
        "tunnelIPv4Address",
        "tunnelIPv4SubnetMask",
        "killSwitchMode",
        "autoConnect",
        "allowLAN",
        "dnsMode",
        "customDNSServers",
        "splitTunnelExclusions",
        "transportPreference",
        "fallbackProxyEnabled",
    ):
        assert field in config
    assert "includeAllNetworks" in manager
    assert "enforceRoutes" in manager
    assert "excludeLocalNetworks" in manager
    assert "NEOnDemandRuleConnect" in manager


def test_packet_tunnel_defaults_are_ipv4_only_for_vps_client() -> None:
    config = _read("native/macos/Shared/BaleTunnelConfiguration.swift")
    provider = _read("native/macos/BaleobalaPacketTunnel/PacketTunnelProvider.swift")

    assert 'tunnelIPv4Address: String = "10.77.0.2"' in config
    assert 'includedIPv6Routes: [String] = []' in config
    assert '"tunnelIPv4Address"' in provider
    assert '"10.7.0.2"' not in provider


def test_build_and_acceptance_scripts_cover_archive_and_validation() -> None:
    build_script = _read("scripts/build-macos.sh")
    acceptance_script = _read("scripts/acceptance-macos-native.sh")
    readme = _read("native/macos/README.md")

    assert "archive" in build_script
    assert "export" in build_script
    assert "xcodebuild" in acceptance_script
    assert "acceptance-macos-native.sh" in readme
    assert "pair enroll" in readme
    assert "request-access" in readme
