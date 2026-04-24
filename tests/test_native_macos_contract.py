from __future__ import annotations

import plistlib
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _match(pattern: str, text: str) -> str:
    found = re.search(pattern, text)
    assert found is not None
    return found.group(1)


def test_macos_app_group_is_consistent() -> None:
    swift = _read("native/macos/Shared/BaleAppGroup.swift")
    app_group = _match(r'static let identifier = "([^"]+)"', swift)

    app_entitlements = plistlib.loads((REPO_ROOT / "native/macos/BaleobalaApp/BaleobalaApp.entitlements").read_bytes())
    tunnel_entitlements = plistlib.loads((REPO_ROOT / "native/macos/BaleobalaPacketTunnel/BaleobalaPacketTunnel.entitlements").read_bytes())

    assert app_group in app_entitlements["com.apple.security.application-groups"]
    assert app_group in tunnel_entitlements["com.apple.security.application-groups"]


def test_packet_tunnel_bundle_identifier_is_consistent() -> None:
    swift = _read("native/macos/Shared/BaleAppGroup.swift")
    provider_bundle = _match(r'static let providerBundleIdentifier = "([^"]+)"', swift)
    xcconfig = _read("native/macos/Config/PacketTunnel.xcconfig")
    info_plist = plistlib.loads((REPO_ROOT / "native/macos/BaleobalaPacketTunnel/Info.plist").read_bytes())
    tunnel_manager = _read("native/macos/Shared/BaleTunnelManager.swift")

    assert provider_bundle in xcconfig
    assert info_plist["CFBundleIdentifier"] in {"$(PRODUCT_BUNDLE_IDENTIFIER)", provider_bundle}
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
    models = _read("native/macos/Shared/BaleAppModels.swift")
    client = _read("native/macos/BaleobalaApp/BaleAppControlClient.swift")
    project = _read("native/macos/Baleobala.xcodeproj/project.pbxproj")

    assert "MenuBarExtra" in app
    for section in ("Overview", "Relays", "Privacy", "Settings", "Diagnostics"):
        assert section in content
    for model in ("BaleAppState", "ConnectionState", "ReadinessGate", "BaleNetworkPolicy", "DiagnosticSnapshot"):
        assert model in models
    assert "baleobala.control.app_control" in client
    assert "BaleAppModels.swift in Sources" in project
    assert "BaleAppControlClient.swift in Sources" in project


def test_tunnel_configuration_carries_network_policy() -> None:
    config = _read("native/macos/Shared/BaleTunnelConfiguration.swift")
    manager = _read("native/macos/Shared/BaleTunnelManager.swift")

    for field in (
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
