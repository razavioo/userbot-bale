"""Product control-plane helpers for auth, pairing, and VPN profiles."""

from baleobala.control.android import (
    activation_request_path,
    carrier_socket_path as android_carrier_socket_path,
    cleanup_stale_carrier_socket as cleanup_android_stale_carrier_socket,
    install_vpn_profile as install_android_vpn_profile,
    load_vpn_profile as load_android_vpn_profile,
    vpn_profile_installed as android_vpn_profile_installed,
    vpn_profile_path as android_vpn_profile_path,
    vpn_service_configuration,
)
from baleobala.control.auth import AuthRecord, AuthStore
from baleobala.control.backend import AndroidVpnBackend, BackendState, MacOSPacketTunnelBackend, ProxyFallbackBackend, VpnBackend, backend_for_profile, default_backend_name
from baleobala.control.keychain import FileSecretBackend, KeychainSecretBackend, SecretBackend
from baleobala.control.credential_watcher import CredentialWatcher
from baleobala.control.mesh import MeshProvisionRecord, MeshProvisionStore
from baleobala.control.pairing import PairingExchange, PairingRecord, PairingStore
from baleobala.control.relay_directory import RelayDirectory, RelayDirectoryEntry
from baleobala.control.macos import MacOSSystemProxySession
from baleobala.control.windows import WindowsSystemProxySession
from baleobala.control.macos_launchd import LaunchAgentSpec, MacOSLaunchAgentManager
from baleobala.control.analyzer import BundleAnalysis, ProductVerdict, analyze_bundle, build_product_verdict, bundle_status, merge_status_with_bundle
from baleobala.control.netns import (
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
from baleobala.control.observability import FailureInfo, StructuredEventRecorder, classify_failure, environment_snapshot, new_run_id, redact_value
from baleobala.control.paths import app_dir, config_dir, data_dir, is_android_runtime
from baleobala.control.probe import ProbeResult, probe_endpoint
from baleobala.control.provisioning import (
    CredentialEpoch,
    DeviceAuthorization,
    ProvisionedPeer,
    ProvisioningError,
    ProvisioningService,
    RelayEnrollment,
)
from baleobala.control.readiness import BackendReadiness
from baleobala.control.scenario import NetnsScenario, build_proxy_pair_scenario, build_tunnel_pair_scenario
from baleobala.control.smoke import SmokeReport, smoke_backend_status
from baleobala.control.service import ConnectionSnapshot, ControlService, ControlSnapshot
from baleobala.control.tunnel_bridge import VpnTunnelBridge
from baleobala.control.tunnel_service import CarrierTunnelService, LocalTunnelService, TunnelBridge, TunnelService, TunnelServiceState
from baleobala.control.vpn import VpnProfile, VpnStore

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
