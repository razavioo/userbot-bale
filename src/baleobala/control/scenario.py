"""Scenario templates that map netns sessions to real baleobala commands."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field


@dataclass(frozen=True)
class NetnsScenario:
    kind: str
    server_cmd: list[str]
    client_cmd: list[str]
    server_ready: str = ""
    client_ready: str = ""
    smoke_client_cmd: str = ""
    expected_markers: list[str] = field(default_factory=list)
    smoke_kind: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "server_cmd": self.server_cmd,
            "client_cmd": self.client_cmd,
            "server_ready": self.server_ready,
            "client_ready": self.client_ready,
            "smoke_client_cmd": self.smoke_client_cmd,
            "expected_markers": self.expected_markers,
            "smoke_kind": self.smoke_kind,
            "notes": self.notes,
        }


def build_proxy_pair_scenario(
    *,
    server_jwt_file: str,
    client_jwt_file: str,
    peer_id: int,
    proxy_secret: str,
    listen_host: str = "127.0.0.1",
    listen_port: int = 1080,
    protocol: str = "fast",
    volume: int = 50,
    identity_prefix: str = "baleobala",
) -> NetnsScenario:
    python = sys.executable
    server_cmd = [
        python, "-m", "baleobala.cli", "bale-proxy", "relay",
        "--answer",
        "--bale-jwt-file", server_jwt_file,
        "--identity", f"{identity_prefix}-relay",
        "--protocol", protocol,
        "--volume", str(volume),
        "--proxy-secret", proxy_secret,
    ]
    client_cmd = [
        python, "-m", "baleobala.cli", "bale-proxy", "client",
        "--bale-jwt-file", client_jwt_file,
        "--peer-id", str(peer_id),
        "--identity", f"{identity_prefix}-client",
        "--listen-host", listen_host,
        "--listen-port", str(listen_port),
        "--protocol", protocol,
        "--volume", str(volume),
        "--proxy-secret", proxy_secret,
    ]
    smoke_client_cmd = (
        "python - <<'PY'\n"
        "import socket\n"
        "s = socket.create_connection(('127.0.0.1', %d), timeout=3)\n"
        "s.sendall(b'ping\\n')\n"
        "print(s.recv(64).decode().strip() or 'proxy-flow-ok')\n"
        "s.close()\n"
        "PY" % listen_port
    )
    return NetnsScenario(
        kind="proxy-pair",
        server_cmd=server_cmd,
        client_cmd=client_cmd,
        smoke_client_cmd=smoke_client_cmd,
        expected_markers=[
            "call_established",
            "transport_selected=",
            "proxy_listening=",
            "teardown_done",
        ],
        smoke_kind="tcp-connect-echo",
        notes=[
            "Client namespace expects the Bale peer id of the relay account.",
            "Smoke command validates a real TCP connect and echo-style payload through the proxy listener.",
            "Readiness markers are stable and should be emitted by the real runtime.",
        ],
    )


def build_tunnel_pair_scenario(
    *,
    server_jwt_file: str,
    client_jwt_file: str,
    peer_id: int,
    server_tun: str = "vpn0",
    client_tun: str = "vpn0",
    server_wan: str = "eth0",
    transport: str = "auto",
    psk_file: str = "",
    identity_prefix: str = "baleobala",
) -> NetnsScenario:
    python = sys.executable
    server_cmd = [
        python, "-m", "baleobala.cli", "tunnel", "exit-node",
        "--answer",
        "--bale-jwt-file", server_jwt_file,
        "--identity", f"{identity_prefix}-relay",
        "--tun", server_tun,
        "--wan", server_wan,
        "--transport", transport,
    ]
    client_cmd = [
        python, "-m", "baleobala.cli", "tunnel", "up",
        "--bale-jwt-file", client_jwt_file,
        "--peer-id", str(peer_id),
        "--identity", f"{identity_prefix}-client",
        "--tun", client_tun,
        "--transport", transport,
    ]
    if psk_file:
        server_cmd.extend(["--psk-file", psk_file])
        client_cmd.extend(["--psk-file", psk_file])
    return NetnsScenario(
        kind="tunnel-pair",
        server_cmd=server_cmd,
        client_cmd=client_cmd,
        expected_markers=[
            "call_established",
            "transport_selected=",
            "tunnel_up=",
            "teardown_done",
        ],
        smoke_kind="tunnel-payload",
        notes=[
            "Both namespaces must already have TUN devices set up with matching names.",
            "Server namespace also needs NAT prerequisites if full egress is part of the test.",
            "Smoke is expected to verify payload flow through the tunnel rather than a template-level command.",
        ],
    )
