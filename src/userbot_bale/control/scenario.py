"""Scenario templates that map netns sessions to real userbot-bale commands."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field


@dataclass(frozen=True)
class NetnsScenario:
    kind: str
    server_cmd: list[str]
    client_cmd: list[str]
    required_markers: list[str] = field(default_factory=list)
    runtime_setup: list[dict[str, object]] = field(default_factory=list)
    smoke_steps: list[list[str]] = field(default_factory=list)
    smoke_kind: str = ""
    bundle_expectations: list[str] = field(default_factory=list)
    failure_hints: dict[str, str] = field(default_factory=dict)
    full_device_checks: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    network_plan: dict[str, object] = field(default_factory=dict)
    server_ready: str = ""
    client_ready: str = ""
    smoke_server_cmd: str = ""
    smoke_client_cmd: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "server_cmd": self.server_cmd,
            "client_cmd": self.client_cmd,
            "required_markers": self.required_markers,
            "runtime_setup": self.runtime_setup,
            "smoke_steps": self.smoke_steps,
            "smoke_kind": self.smoke_kind,
            "bundle_expectations": self.bundle_expectations,
            "failure_hints": self.failure_hints,
            "full_device_checks": self.full_device_checks,
            "notes": self.notes,
            "network_plan": self.network_plan,
            "server_ready": self.server_ready,
            "client_ready": self.client_ready,
            "smoke_server_cmd": self.smoke_server_cmd,
            "smoke_client_cmd": self.smoke_client_cmd,
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
    identity_prefix: str = "userbot-bale",
) -> NetnsScenario:
    python = sys.executable
    server_cmd = [
        python, "-m", "userbot_bale.cli", "bale-proxy", "relay",
        "--answer",
        "--bale-jwt-file", server_jwt_file,
        "--identity", f"{identity_prefix}-relay",
        "--protocol", protocol,
        "--volume", str(volume),
        "--proxy-secret", proxy_secret,
    ]
    client_cmd = [
        python, "-m", "userbot_bale.cli", "bale-proxy", "client",
        "--bale-jwt-file", client_jwt_file,
        "--peer-id", str(peer_id),
        "--identity", f"{identity_prefix}-client",
        "--listen-host", listen_host,
        "--listen-port", str(listen_port),
        "--protocol", protocol,
        "--volume", str(volume),
        "--proxy-secret", proxy_secret,
    ]
    smoke_server_cmd = (
        "python - <<'PY'\n"
        "import socket\n"
        "listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        "listener.bind(('0.0.0.0', 18080))\n"
        "listener.listen(1)\n"
        "conn, _addr = listener.accept()\n"
        "payload = conn.recv(64)\n"
        "conn.sendall(payload or b'proxy-flow-ok\\n')\n"
        "conn.close()\n"
        "listener.close()\n"
        "PY"
    )
    smoke_client_cmd = (
        "python - <<'PY'\n"
        "import socket, struct\n"
        "proxy = socket.create_connection(('127.0.0.1', %d), timeout=3)\n"
        "proxy.sendall(b'\\x05\\x01\\x00')\n"
        "assert proxy.recv(2) == b'\\x05\\x00'\n"
        "proxy.sendall(b'\\x05\\x01\\x00\\x01' + socket.inet_aton('172.29.0.2') + struct.pack('>H', 18080))\n"
        "reply = proxy.recv(10)\n"
        "assert len(reply) >= 2 and reply[1] == 0, reply\n"
        "proxy.sendall(b'proxy-flow-ok\\n')\n"
        "print(proxy.recv(64).decode().strip())\n"
        "proxy.close()\n"
        "PY" % listen_port
    )
    return NetnsScenario(
        kind="proxy-pair",
        server_cmd=server_cmd,
        client_cmd=client_cmd,
        smoke_server_cmd=smoke_server_cmd,
        smoke_client_cmd=smoke_client_cmd,
        required_markers=[
            "call_established",
            "transport_selected=",
            "proxy_listening=",
            "teardown_done",
        ],
        runtime_setup=[
            {"phase": "server", "action": "start_echo_target", "host": "127.0.0.1", "port": 18080},
        ],
        smoke_steps=[
            ["python", "-c", "import socket, struct"],
            ["python", "-c", "print('proxy-flow-ok')"],
        ],
        smoke_kind="tcp-connect-echo",
        bundle_expectations=[
            "verdict.json",
            "summary.json",
            "scenario.json",
            "environment.json",
            "events.jsonl",
            "process_status.json",
            "markers.json",
            "smoke.json",
            "teardown.json",
            "server.log",
            "client.log",
            "route_snapshot.json",
            "dns_snapshot.json",
            "command_transcript.json",
        ],
        failure_hints={
            "call_setup_timeout": "wait for call_established before smoke",
            "payload_probe_failed": "proxy CONNECT or echo round-trip failed",
        },
        notes=[
            "Client namespace expects the Bale peer id of the relay account.",
            "Smoke command validates a real SOCKS5 CONNECT and echo-style payload through the proxy listener.",
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
    identity_prefix: str = "userbot-bale",
    full_device: bool = True,
    dns_servers: tuple[str, ...] = ("1.1.1.1", "9.9.9.9"),
    carrier_hosts: tuple[str, ...] = ("next-ws.bale.ai",),
    skip_nat_setup: bool = False,
    skip_host_route_setup: bool = False,
) -> NetnsScenario:
    python = sys.executable
    server_cmd = [
        python, "-m", "userbot_bale.cli", "tunnel", "exit-node",
        "--answer",
        "--bale-jwt-file", server_jwt_file,
        "--identity", f"{identity_prefix}-relay",
        "--tun", server_tun,
        "--wan", server_wan,
        "--transport", transport,
    ]
    client_cmd = [
        python, "-m", "userbot_bale.cli", "tunnel", "up",
        "--bale-jwt-file", client_jwt_file,
        "--peer-id", str(peer_id),
        "--identity", f"{identity_prefix}-client",
        "--tun", client_tun,
        "--transport", transport,
    ]
    if psk_file:
        server_cmd.extend(["--psk-file", psk_file])
        client_cmd.extend(["--psk-file", psk_file])
    smoke_lines = [
        "ping -c 1 10.77.0.1",
        f"{python} - <<'PY'",
        "import socket",
    ]
    if full_device:
        smoke_lines.extend(
            [
                "socket.gethostbyname('example.com')",
                "s = socket.create_connection(('1.1.1.1', 53), timeout=3)",
                "s.close()",
            ]
        )
    smoke_lines.extend(
        [
            "print('tunnel-flow-ok')",
            "PY",
        ]
    )
    smoke_client_cmd = " && ".join(
        [
            smoke_lines[0],
            "\n".join(smoke_lines[1:]),
        ]
    )
    return NetnsScenario(
        kind="tunnel-pair",
        server_cmd=server_cmd,
        client_cmd=client_cmd,
        required_markers=[
            "call_established",
            "transport_selected=",
            "tunnel_up=",
            "teardown_done",
        ],
        runtime_setup=[
            {"phase": "client", "action": "create_tun", "tun": client_tun, "address": "10.77.0.2/24", "mtu": 1400},
            {"phase": "server", "action": "create_tun", "tun": server_tun, "address": "10.77.0.1/24", "mtu": 1400},
            {"phase": "server", "action": "enable_nat", "wan": server_wan, "enabled": not skip_nat_setup},
        ],
        smoke_steps=[["ping", "-c", "1", "10.77.0.1"]],
        smoke_client_cmd=smoke_client_cmd,
        smoke_kind="tunnel-payload",
        bundle_expectations=[
            "verdict.json",
            "summary.json",
            "scenario.json",
            "environment.json",
            "events.jsonl",
            "process_status.json",
            "markers.json",
            "smoke.json",
            "teardown.json",
            "server.log",
            "client.log",
            "route_snapshot.json",
            "dns_snapshot.json",
            "command_transcript.json",
        ],
        failure_hints={
            "call_setup_timeout": "tunnel_up never arrived",
            "payload_probe_failed": "tunnel packet probe or full-device assertion failed",
            "missing_tun": "TUN device support is required",
        },
        full_device_checks=["route", "dns", "tcp-egress"] if full_device else [],
        notes=[
            "The session runner is expected to create/configure both TUN devices when automation is enabled.",
            "Server-side NAT and client-side default-route/DNS setup are part of the session orchestration.",
            "Smoke is expected to verify payload flow through the tunnel plus DNS and egress probes in full-device mode.",
        ],
        network_plan={
            "mode": "linux-full-device" if full_device else "linux-tunnel-only",
            "full_device": full_device,
            "dns_servers": list(dns_servers),
            "client": {
                "tun": client_tun,
                "address": "10.77.0.2/24",
                "mtu": 1400,
                "routes": ["0.0.0.0/1", "128.0.0.0/1"] if full_device else [],
            },
            "server": {
                "tun": server_tun,
                "address": "10.77.0.1/24",
                "mtu": 1400,
                "wan": server_wan,
                "enable_nat": not skip_nat_setup,
            },
            "carrier_hosts": list(carrier_hosts),
            "setup_host_routes": not skip_host_route_setup,
            "dns_probe_host": "example.com" if full_device else "",
            "egress_probe_host": "1.1.1.1" if full_device else "",
            "egress_probe_port": 53 if full_device else 0,
        },
    )
