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

    assert provider_bundle in xcconfig
    assert info_plist["CFBundleIdentifier"] in {"$(PRODUCT_BUNDLE_IDENTIFIER)", provider_bundle}


def test_packet_tunnel_provider_implements_control_status_contract() -> None:
    provider = _read("native/macos/BaleobalaPacketTunnel/PacketTunnelProvider.swift")
    assert "handleControlMessage" in provider
    assert '"type": "status"' in provider
    assert '"type": "pong"' in provider
    assert "statusVersion" in provider


def test_build_and_acceptance_scripts_cover_archive_and_validation() -> None:
    build_script = _read("scripts/build-macos.sh")
    acceptance_script = _read("scripts/acceptance-macos-native.sh")
    readme = _read("native/macos/README.md")

    assert "archive" in build_script
    assert "export" in build_script
    assert "xcodebuild" in acceptance_script
    assert "acceptance-macos-native.sh" in readme
    assert "pair export-request" in readme
