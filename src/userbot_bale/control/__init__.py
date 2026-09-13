"""Product control-plane helpers for auth, pairing, and VPN profiles."""

from userbot_bale.control.android import (
    activation_request_path,
    carrier_socket_path as android_carrier_socket_path,
    cleanup_stale_carrier_socket as cleanup_android_stale_carrier_socket,
    install_vpn_profile as install_android_vpn_profile,
    load_vpn_profile as load_android_vpn_profile,
    vpn_profile_installed as android_vpn_profile_installed,
    vpn_profile_path as android_vpn_profile_path,
    vpn_service_configuration,
)
from userbot_bale.control.auth import AuthRecord, AuthStore
from userbot_bale.control.backend import AndroidVpnBackend, BackendState, MacOSPacketTunnelBackend, ProxyFallbackBackend, VpnBackend, backend_for_profile, default_backend_name
from userbot_bale.control.keychain import FileSecretBackend, KeychainSecretBackend, KeyringSecretBackend, SecretBackend
from userbot_bale.control.credential_watcher import CredentialWatcher
from userbot_bale.control.mesh import MeshProvisionRecord, MeshProvisionStore
from userbot_bale.control.pairing import PairingExchange, PairingRecord, PairingStore
from userbot_bale.control.relay_directory import RelayDirectory, RelayDirectoryEntry
from userbot_bale.control.macos import MacOSSystemProxySession
from userbot_bale.control.windows import WindowsSystemProxySession
from userbot_bale.control.macos_launchd import LaunchAgentSpec, MacOSLaunchAgentManager
from userbot_bale.control.analyzer import BundleAnalysis, ProductVerdict, analyze_bundle, build_product_verdict, bundle_status, merge_status_with_bundle
from userbot_bale.control.netns import (
    NetnsHarness,
    NetnsProcessManager,
    NetnsProcessSpec,
    NetnsProcessStatus,
    NetnsSessionReport,
    NetnsSessionRunner,
    NetnsRunReport,
    NetnsStepResult,
    NetnsTopology,
    render_process_script,
    render_setup_commands,
    render_shell_script,
    render_smoke_commands,
    render_teardown_commands,
)
from userbot_bale.control.observability import FailureInfo, StructuredEventRecorder, classify_failure, environment_snapshot, new_run_id, redact_value
from userbot_bale.control.paths import app_dir, config_dir, data_dir, is_android_runtime
from userbot_bale.control.probe import ProbeResult, probe_endpoint
from userbot_bale.control.provisioning import (
    CredentialEpoch,
    DeviceAuthorization,
    ProvisionedPeer,
    ProvisioningError,
    ProvisioningService,
    RelayEnrollment,
)
from userbot_bale.control.readiness import BackendReadiness
from userbot_bale.control.scenario import NetnsScenario, build_proxy_pair_scenario, build_tunnel_pair_scenario
from userbot_bale.control.smoke import SmokeReport, smoke_backend_status
from userbot_bale.control.service import ConnectionSnapshot, ControlService, ControlSnapshot
from userbot_bale.control.tunnel_bridge import VpnTunnelBridge
from userbot_bale.control.tunnel_service import CarrierTunnelService, LocalTunnelService, TunnelBridge, TunnelService, TunnelServiceState
from userbot_bale.control.vpn import VpnProfile, VpnStore

__all__ = [
    "app_dir",
    "config_dir",
    "data_dir",
    "is_android_runtime",
    "AuthRecord",
    "AuthStore",
    "AndroidVpnBackend",
    "BackendState",
    "MacOSPacketTunnelBackend",
    "PairingRecord",
    "PairingExchange",
    "PairingStore",
    "RelayDirectory",
    "RelayDirectoryEntry",
    "MeshProvisionRecord",
    "MeshProvisionStore",
    "MacOSSystemProxySession",
    "WindowsSystemProxySession",
    "ProxyFallbackBackend",
    "NetnsTopology",
    "NetnsHarness",
    "NetnsProcessManager",
    "NetnsProcessSpec",
    "NetnsProcessStatus",
    "NetnsSessionReport",
    "NetnsSessionRunner",
    "NetnsRunReport",
    "NetnsStepResult",
    "render_process_script",
    "render_setup_commands",
    "render_smoke_commands",
    "render_teardown_commands",
    "render_shell_script",
    "ProbeResult",
    "probe_endpoint",
    "RelayEnrollment",
    "DeviceAuthorization",
    "CredentialEpoch",
    "ProvisionedPeer",
    "ProvisioningError",
    "ProvisioningService",
    "BackendReadiness",
    "CredentialWatcher",
    "NetnsScenario",
    "build_proxy_pair_scenario",
    "build_tunnel_pair_scenario",
    "SmokeReport",
    "smoke_backend_status",
    "BundleAnalysis",
    "ProductVerdict",
    "analyze_bundle",
    "bundle_status",
    "merge_status_with_bundle",
    "build_product_verdict",
    "FailureInfo",
    "StructuredEventRecorder",
    "classify_failure",
    "environment_snapshot",
    "new_run_id",
    "redact_value",
    "LaunchAgentSpec",
    "MacOSLaunchAgentManager",
    "ControlService",
    "ControlSnapshot",
    "ConnectionSnapshot",
    "SecretBackend",
    "FileSecretBackend",
    "KeychainSecretBackend",
    "KeyringSecretBackend",
    "TunnelService",
    "TunnelServiceState",
    "TunnelBridge",
    "VpnTunnelBridge",
    "LocalTunnelService",
    "CarrierTunnelService",
    "android_vpn_profile_path",
    "load_android_vpn_profile",
    "android_vpn_profile_installed",
    "install_android_vpn_profile",
    "activation_request_path",
    "vpn_service_configuration",
    "android_carrier_socket_path",
    "cleanup_android_stale_carrier_socket",
    "VpnBackend",
    "backend_for_profile",
    "default_backend_name",
    "VpnProfile",
    "VpnStore",
]
