"""Product control-plane helpers for auth, pairing, and VPN profiles."""

from baleobala.control.auth import AuthRecord, AuthStore
from baleobala.control.backend import BackendState, MacOSPacketTunnelBackend, ProxyFallbackBackend, VpnBackend, backend_for_profile, default_backend_name
from baleobala.control.keychain import FileSecretBackend, KeychainSecretBackend, SecretBackend
from baleobala.control.mesh import MeshProvisionRecord, MeshProvisionStore
from baleobala.control.pairing import PairingExchange, PairingRecord, PairingStore
from baleobala.control.macos import MacOSSystemProxySession
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
from baleobala.control.paths import app_dir, config_dir, data_dir
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
from baleobala.control.tunnel_service import CarrierTunnelService, LocalTunnelService, TunnelBridge, TunnelService, TunnelServiceState
from baleobala.control.vpn import VpnProfile, VpnStore

__all__ = [
    "app_dir",
    "config_dir",
    "data_dir",
    "AuthRecord",
    "AuthStore",
    "BackendState",
    "MacOSPacketTunnelBackend",
    "PairingRecord",
    "PairingExchange",
    "PairingStore",
    "MeshProvisionRecord",
    "MeshProvisionStore",
    "MacOSSystemProxySession",
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
    "LocalTunnelService",
    "CarrierTunnelService",
    "VpnBackend",
    "backend_for_profile",
    "default_backend_name",
    "VpnProfile",
    "VpnStore",
]
