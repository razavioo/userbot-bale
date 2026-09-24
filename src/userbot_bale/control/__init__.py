"""Product control-plane helpers for auth, pairing, and VPN profiles."""
import importlib

_MODULE_MAP = {
    "activation_request_path": "userbot_bale.control.android",
    "android_carrier_socket_path": "userbot_bale.control.android",
    "cleanup_android_stale_carrier_socket": "userbot_bale.control.android",
    "install_android_vpn_profile": "userbot_bale.control.android",
    "load_android_vpn_profile": "userbot_bale.control.android",
    "android_vpn_profile_installed": "userbot_bale.control.android",
    "android_vpn_profile_path": "userbot_bale.control.android",
    "vpn_service_configuration": "userbot_bale.control.android",
    "carrier_socket_path": "userbot_bale.control.android",
    "cleanup_stale_carrier_socket": "userbot_bale.control.android",
    "install_vpn_profile": "userbot_bale.control.android",
    "load_vpn_profile": "userbot_bale.control.android",
    "vpn_profile_installed": "userbot_bale.control.android",
    "vpn_profile_path": "userbot_bale.control.android",
    "AuthRecord": "userbot_bale.control.auth",
    "AuthStore": "userbot_bale.control.auth",
    "AndroidVpnBackend": "userbot_bale.control.backend",
    "BackendState": "userbot_bale.control.backend",
    "MacOSPacketTunnelBackend": "userbot_bale.control.backend",
    "ProxyFallbackBackend": "userbot_bale.control.backend",
    "VpnBackend": "userbot_bale.control.backend",
    "backend_for_profile": "userbot_bale.control.backend",
    "default_backend_name": "userbot_bale.control.backend",
    "FileSecretBackend": "userbot_bale.control.keychain",
    "KeychainSecretBackend": "userbot_bale.control.keychain",
    "KeyringSecretBackend": "userbot_bale.control.keychain",
    "SecretBackend": "userbot_bale.control.keychain",
    "CredentialWatcher": "userbot_bale.control.credential_watcher",
    "MeshProvisionRecord": "userbot_bale.control.mesh",
    "MeshProvisionStore": "userbot_bale.control.mesh",
    "PairingExchange": "userbot_bale.control.pairing",
    "PairingRecord": "userbot_bale.control.pairing",
    "PairingStore": "userbot_bale.control.pairing",
    "RelayDirectory": "userbot_bale.control.relay_directory",
    "RelayDirectoryEntry": "userbot_bale.control.relay_directory",
    "MacOSSystemProxySession": "userbot_bale.control.macos",
    "WindowsSystemProxySession": "userbot_bale.control.windows",
    "LaunchAgentSpec": "userbot_bale.control.macos_launchd",
    "MacOSLaunchAgentManager": "userbot_bale.control.macos_launchd",
    "BundleAnalysis": "userbot_bale.control.analyzer",
    "ProductVerdict": "userbot_bale.control.analyzer",
    "analyze_bundle": "userbot_bale.control.analyzer",
    "build_product_verdict": "userbot_bale.control.analyzer",
    "bundle_status": "userbot_bale.control.analyzer",
    "merge_status_with_bundle": "userbot_bale.control.analyzer",
    "NetnsHarness": "userbot_bale.control.netns",
    "NetnsProcessManager": "userbot_bale.control.netns",
    "NetnsProcessSpec": "userbot_bale.control.netns",
    "NetnsProcessStatus": "userbot_bale.control.netns",
    "NetnsSessionReport": "userbot_bale.control.netns",
    "NetnsSessionRunner": "userbot_bale.control.netns",
    "NetnsRunReport": "userbot_bale.control.netns",
    "NetnsStepResult": "userbot_bale.control.netns",
    "NetnsTopology": "userbot_bale.control.netns",
    "render_process_script": "userbot_bale.control.netns",
    "render_setup_commands": "userbot_bale.control.netns",
    "render_shell_script": "userbot_bale.control.netns",
    "render_smoke_commands": "userbot_bale.control.netns",
    "render_teardown_commands": "userbot_bale.control.netns",
    "FailureInfo": "userbot_bale.control.observability",
    "StructuredEventRecorder": "userbot_bale.control.observability",
    "classify_failure": "userbot_bale.control.observability",
    "environment_snapshot": "userbot_bale.control.observability",
    "new_run_id": "userbot_bale.control.observability",
    "redact_value": "userbot_bale.control.observability",
    "app_dir": "userbot_bale.control.paths",
    "config_dir": "userbot_bale.control.paths",
    "data_dir": "userbot_bale.control.paths",
    "is_android_runtime": "userbot_bale.control.paths",
    "ProbeResult": "userbot_bale.control.probe",
    "probe_endpoint": "userbot_bale.control.probe",
    "CredentialEpoch": "userbot_bale.control.provisioning",
    "DeviceAuthorization": "userbot_bale.control.provisioning",
    "ProvisionedPeer": "userbot_bale.control.provisioning",
    "ProvisioningError": "userbot_bale.control.provisioning",
    "ProvisioningService": "userbot_bale.control.provisioning",
    "RelayEnrollment": "userbot_bale.control.provisioning",
    "BackendReadiness": "userbot_bale.control.readiness",
    "NetnsScenario": "userbot_bale.control.scenario",
    "build_proxy_pair_scenario": "userbot_bale.control.scenario",
    "build_tunnel_pair_scenario": "userbot_bale.control.scenario",
    "SmokeReport": "userbot_bale.control.smoke",
    "smoke_backend_status": "userbot_bale.control.smoke",
    "ConnectionSnapshot": "userbot_bale.control.service",
    "ControlService": "userbot_bale.control.service",
    "ControlSnapshot": "userbot_bale.control.service",
    "VpnTunnelBridge": "userbot_bale.control.tunnel_bridge",
    "CarrierTunnelService": "userbot_bale.control.tunnel_service",
    "LocalTunnelService": "userbot_bale.control.tunnel_service",
    "TunnelBridge": "userbot_bale.control.tunnel_service",
    "TunnelService": "userbot_bale.control.tunnel_service",
    "TunnelServiceState": "userbot_bale.control.tunnel_service",
    "VpnProfile": "userbot_bale.control.vpn",
    "VpnStore": "userbot_bale.control.vpn",
}

__all__ = list(_MODULE_MAP.keys())


def __getattr__(name: str):
    if name in _MODULE_MAP:
        mod = importlib.import_module(_MODULE_MAP[name])
        attr_name = name
        if name == "android_carrier_socket_path":
            attr_name = "carrier_socket_path"
        elif name == "cleanup_android_stale_carrier_socket":
            attr_name = "cleanup_stale_carrier_socket"
        elif name == "install_android_vpn_profile":
            attr_name = "install_vpn_profile"
        elif name == "load_android_vpn_profile":
            attr_name = "load_vpn_profile"
        elif name == "android_vpn_profile_installed":
            attr_name = "vpn_profile_installed"
        elif name == "android_vpn_profile_path":
            attr_name = "vpn_profile_path"
        val = getattr(mod, attr_name)
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
