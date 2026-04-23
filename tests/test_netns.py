from __future__ import annotations

from baleobala.control.netns import (
    NetnsHarness,
    NetnsProcessManager,
    NetnsProcessSpec,
    NetnsSessionRunner,
    NetnsTopology,
    render_process_script,
    render_setup_commands,
    render_shell_script,
    render_smoke_commands,
    render_teardown_commands,
)
from baleobala.control.scenario import build_proxy_pair_scenario
import json
import subprocess


def test_netns_topology_uses_prefix() -> None:
    topology = NetnsTopology.with_prefix("baleobala")
    assert topology.client_ns.endswith("-client")
    assert topology.server_ns.endswith("-server")
    assert topology.client_veth.endswith("-vc")
    assert topology.server_veth.endswith("-vs")


def test_netns_rendered_commands_cover_setup_smoke_teardown() -> None:
    topology = NetnsTopology.with_prefix("bb")
    setup = render_setup_commands(topology)
    smoke = render_smoke_commands(topology)
    teardown = render_teardown_commands(topology)
    assert setup[0] == ["ip", "netns", "add", topology.client_ns]
    assert any(cmd[:4] == ["ip", "netns", "exec", topology.client_ns] for cmd in setup)
    assert smoke[0][-1] == topology.server_ip
    assert teardown[-1] == ["ip", "netns", "del", topology.server_ns]


def test_netns_shell_script_contains_sections() -> None:
    script = render_shell_script(NetnsTopology.with_prefix("bb"))
    assert "# setup" in script
    assert "# smoke" in script
    assert "# teardown" in script
    assert "ip netns add bb-client" in script


class FakeNetnsRunner:
    def __init__(self, fail_on: tuple[str, ...] = ()) -> None:
        self.calls: list[list[str]] = []
        self._fail_on = fail_on

    def __call__(self, cmd, check=False, capture_output=True, text=True):  # noqa: ARG002
        self.calls.append(list(cmd))
        cmd_text = " ".join(cmd)
        for marker in self._fail_on:
            if marker in cmd_text:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=f"failed on {marker}")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")


def test_netns_harness_full_cycle_records_steps(tmp_path) -> None:
    harness = NetnsHarness(
        NetnsTopology.with_prefix("bb"),
        runner=FakeNetnsRunner(),
        state_path=tmp_path / "netns.json",
    )
    report = harness.full_cycle()
    assert report.ok
    assert report.stage == "full-cycle"
    assert any(step["phase"] == "setup" for step in report.steps)
    assert any(step["phase"] == "smoke" for step in report.steps)
    assert any(step["phase"] == "teardown" for step in report.steps)
    assert harness.status() is not None


def test_netns_harness_stops_on_setup_failure(tmp_path) -> None:
    harness = NetnsHarness(
        NetnsTopology.with_prefix("bb"),
        runner=FakeNetnsRunner(fail_on=("link add",)),
        state_path=tmp_path / "netns.json",
    )
    report = harness.setup()
    assert not report.ok
    assert "failed on link add" in report.last_error


def test_netns_harness_builds_wrapped_process_specs(tmp_path) -> None:
    harness = NetnsHarness(
        NetnsTopology.with_prefix("bb"),
        runner=FakeNetnsRunner(),
        state_path=tmp_path / "netns.json",
        logs_dir=tmp_path / "logs",
    )
    specs = harness.build_process_specs(
        server_cmd=["sh", "-lc", "echo server"],
        client_cmd=["sh", "-lc", "echo client"],
    )
    assert len(specs) == 2
    assert specs[0].command[:4] == ["ip", "netns", "exec", "bb-server"]
    assert specs[1].command[:4] == ["ip", "netns", "exec", "bb-client"]
    assert specs[0].log_path.endswith("bb-server.log")


def test_render_process_script_contains_wrapped_process_commands() -> None:
    script = render_process_script(
        NetnsTopology.with_prefix("bb"),
        server_cmd=["sh", "-lc", "echo server"],
        client_cmd=["sh", "-lc", "echo client"],
        logs_dir="/tmp/bb-logs",
    )
    assert "# process-launch" in script
    assert "ip netns exec bb-server sh -lc 'echo server'" in script
    assert "ip netns exec bb-client sh -lc 'echo client'" in script


class FakePopen:
    _next_pid = 4000

    def __init__(self, cmd, stdout=None, stderr=None):  # noqa: ARG002
        self.cmd = list(cmd)
        self.pid = FakePopen._next_pid
        FakePopen._next_pid += 1
        self._terminated = False

    def poll(self):
        return 0 if self._terminated else None

    def terminate(self):
        self._terminated = True


class FakeReadyPopen(FakePopen):
    def __init__(self, cmd, stdout=None, stderr=None):  # noqa: ARG002
        super().__init__(cmd, stdout=stdout, stderr=stderr)
        if stdout is not None:
            stdout.write(b"call_established\ntransport_selected=dc\nproxy_listening=127.0.0.1:1080\ntunnel_up=vpn0\nteardown_done\n")
            stdout.flush()


def test_marker_parser_uses_exact_lines_only() -> None:
    from baleobala.control.netns import _parse_marker_lines

    text = "noise call_established noise\ncall_established\ntransport_selected=dc\nrandom proxy_listening=1.2.3.4:5 text\nproxy_listening=127.0.0.1:1080\n"
    markers = _parse_marker_lines(text)
    assert "call_established" in markers
    assert "transport_selected=dc" in markers
    assert "proxy_listening=127.0.0.1:1080" in markers
    assert all("noise" not in item for item in markers)


def test_marker_parser_deduplicates_and_ignores_substrings() -> None:
    from baleobala.control.netns import _parse_marker_lines

    text = "call_established\ncall_established\nteardown_done\nnot-a-teardown_done-marker\n"
    markers = _parse_marker_lines(text)
    assert markers.count("call_established") == 1
    assert markers.count("teardown_done") == 1


def test_netns_process_manager_start_stop_status(tmp_path) -> None:
    manager = NetnsProcessManager(
        state_path=tmp_path / "proc.json",
        popen_factory=FakePopen,
    )
    specs = [
        NetnsProcessSpec(
            namespace="bb-server",
            name="server",
            command=["ip", "netns", "exec", "bb-server", "echo", "server"],
            log_path=str(tmp_path / "server.log"),
        ),
        NetnsProcessSpec(
            namespace="bb-client",
            name="client",
            command=["ip", "netns", "exec", "bb-client", "echo", "client"],
            log_path=str(tmp_path / "client.log"),
        ),
    ]
    started = manager.start(specs)
    assert len(started) == 2
    assert all(item.running for item in started)
    status = manager.status()
    assert len(status) == 2
    assert status[0].running
    stopped = manager.stop()
    assert len(stopped) == 2


def test_netns_session_runner_full_success(tmp_path) -> None:
    harness = NetnsHarness(
        NetnsTopology.with_prefix("bb"),
        runner=FakeNetnsRunner(),
        state_path=tmp_path / "netns.json",
        logs_dir=tmp_path / "logs",
    )
    manager = NetnsProcessManager(
        state_path=tmp_path / "proc.json",
        popen_factory=FakeReadyPopen,
    )
    artifact_root = tmp_path / "artifacts"
    session = NetnsSessionRunner(harness, manager, artifact_root=artifact_root)
    report = session.run(
        server_cmd=["sh", "-lc", "echo ready"],
        client_cmd=["sh", "-lc", "echo ready"],
        scenario={"required_markers": ["call_established", "transport_selected="], "smoke_kind": "tcp-connect-echo"},
        timeout=0.2,
    )
    assert report.ok
    assert report.readiness == "ready"
    assert report.call_established == "yes"
    assert report.transport_selected == "dc"
    assert report.data_flow_ok == "yes"
    assert report.teardown_clean == "yes"
    assert report.tun_created == "no"
    assert report.artifact_bundle
    assert report.run_id
    assert report.failure_code == ""
    assert "call_established" in report.log_tails["server"]
    bundle = next(artifact_root.glob("bb-*"))
    verdict = json.loads((bundle / "verdict.json").read_text(encoding="utf-8"))
    assert "transport_selected=dc" in verdict["markers"]
    assert verdict["failure_class"] == ""
    assert verdict["failure_code"] == ""
    assert (bundle / "summary.json").exists()
    assert (bundle / "events.jsonl").exists()
    assert (bundle / "route_snapshot.json").exists()
    assert (bundle / "dns_snapshot.json").exists()
    assert (bundle / "command_transcript.json").exists()
    assert (bundle / "server.log").exists()
    assert (bundle / "client.log").exists()
    summary = json.loads((bundle / "summary.json").read_text(encoding="utf-8"))
    assert summary["smoke_kind"] == "tcp-connect-echo"


def test_netns_session_runner_times_out_when_patterns_never_appear(tmp_path) -> None:
    harness = NetnsHarness(
        NetnsTopology.with_prefix("bb"),
        runner=FakeNetnsRunner(),
        state_path=tmp_path / "netns.json",
        logs_dir=tmp_path / "logs",
    )
    manager = NetnsProcessManager(
        state_path=tmp_path / "proc.json",
        popen_factory=FakePopen,
    )
    session = NetnsSessionRunner(harness, manager)
    report = session.run(
        server_cmd=["sh", "-lc", "echo nope"],
        client_cmd=["sh", "-lc", "echo nope"],
        scenario={"required_markers": ["call_established"]},
        timeout=0.1,
    )
    assert not report.ok
    assert report.readiness == "timeout"
    assert report.last_error == "process readiness timeout"
    assert report.failure_class == "call_setup"
    assert report.failure_code == "call_setup_timeout"


def test_netns_session_runner_uses_custom_smoke_commands(tmp_path) -> None:
    runner = FakeNetnsRunner()
    harness = NetnsHarness(
        NetnsTopology.with_prefix("bb"),
        runner=runner,
        state_path=tmp_path / "netns.json",
        logs_dir=tmp_path / "logs",
    )
    manager = NetnsProcessManager(
        state_path=tmp_path / "proc.json",
        popen_factory=FakeReadyPopen,
    )
    session = NetnsSessionRunner(harness, manager)
    report = session.run(
        server_cmd=["sh", "-lc", "echo ready"],
        client_cmd=["sh", "-lc", "echo ready"],
        scenario={"required_markers": ["call_established"]},
        smoke_commands=[["ip", "netns", "exec", "bb-client", "sh", "-lc", "echo smoke"]],
        timeout=0.2,
    )
    assert report.ok
    assert any(step["phase"] == "smoke" for step in report.harness_steps)


def test_netns_scenario_includes_verdict_markers() -> None:
    scenario = build_proxy_pair_scenario(
        server_jwt_file="/tmp/server.jwt",
        client_jwt_file="/tmp/client.jwt",
        peer_id=7,
        proxy_secret="secret",
    )
    payload = scenario.to_dict()
    assert "required_markers" in payload
    assert "smoke_kind" in payload
    assert "smoke_server_cmd" in payload


def test_proxy_scenario_uses_real_socks_smoke_commands() -> None:
    scenario = build_proxy_pair_scenario(
        server_jwt_file="/tmp/server.jwt",
        client_jwt_file="/tmp/client.jwt",
        peer_id=7,
        proxy_secret="secret",
    )
    assert "listener.bind(('0.0.0.0', 18080))" in scenario.smoke_server_cmd
    assert "proxy.sendall(b'\\x05\\x01\\x00')" in scenario.smoke_client_cmd
    assert "transport_selected=" not in scenario.smoke_client_cmd


def test_netns_session_runner_applies_tunnel_network_plan(tmp_path) -> None:
    runner = FakeNetnsRunner()
    harness = NetnsHarness(
        NetnsTopology.with_prefix("bb"),
        runner=runner,
        state_path=tmp_path / "netns.json",
        logs_dir=tmp_path / "logs",
    )
    manager = NetnsProcessManager(
        state_path=tmp_path / "proc.json",
        popen_factory=FakeReadyPopen,
    )
    session = NetnsSessionRunner(harness, manager, artifact_root=tmp_path / "artifacts")
    report = session.run(
        server_cmd=["sh", "-lc", "echo ready"],
        client_cmd=["sh", "-lc", "echo ready"],
        scenario={
            "kind": "tunnel-pair",
            "smoke_kind": "tunnel-payload",
            "required_markers": ["call_established", "transport_selected=", "tunnel_up="],
            "network_plan": {
                "full_device": True,
                "dns_servers": ["1.1.1.1"],
                "carrier_hosts": ["next-ws.bale.ai"],
                "setup_host_routes": True,
                "client": {
                    "tun": "vpn0",
                    "address": "10.77.0.2/24",
                    "mtu": 1400,
                    "routes": ["0.0.0.0/1", "128.0.0.0/1"],
                },
                "server": {
                    "tun": "vpn0",
                    "address": "10.77.0.1/24",
                    "mtu": 1400,
                    "wan": "eth0",
                    "enable_nat": True,
                },
            },
        },
        smoke_commands=[["ip", "netns", "exec", "bb-client", "sh", "-lc", "echo tunnel-smoke"]],
        timeout=0.2,
    )
    assert report.ok
    assert report.tun_created == "yes"
    assert report.routes_programmed == "yes"
    assert report.dns_configured == "yes"
    assert report.nat_configured == "yes"
    assert report.carrier_bypass_configured == "yes"
    assert report.default_route_active == "yes"
    assert report.dns_probe_ok == "yes"
    assert report.egress_probe_ok == "yes"
    commands = [" ".join(cmd) for cmd in runner.calls]
    assert any("ip netns exec bb-client ip tuntap add dev vpn0 mode tun" in cmd for cmd in commands)
    assert any("ip netns exec bb-server iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE" in cmd for cmd in commands)
    assert any("ip netns exec bb-client ip route replace next-ws.bale.ai via 172.29.0.2 dev bb-vc" in cmd for cmd in commands)
    assert any("ip netns exec bb-client ip route replace default via 10.77.0.1 dev vpn0 metric 50" in cmd for cmd in commands)
    assert any("mkdir -p /etc/netns/bb-client" in cmd for cmd in commands)
    bundle = next((tmp_path / "artifacts").glob("bb-*"))
    summary = json.loads((bundle / "summary.json").read_text(encoding="utf-8"))
    snapshots = json.loads((bundle / "route_snapshot.json").read_text(encoding="utf-8"))
    dns_snapshots = json.loads((bundle / "dns_snapshot.json").read_text(encoding="utf-8"))
    assert summary["smoke_kind"] == "tunnel-payload"
    assert "client" in snapshots
    assert "client" in dns_snapshots


def test_netns_session_runner_classifies_nat_setup_failures(tmp_path) -> None:
    runner = FakeNetnsRunner(fail_on=("iptables -t nat -A POSTROUTING",))
    harness = NetnsHarness(
        NetnsTopology.with_prefix("bb"),
        runner=runner,
        state_path=tmp_path / "netns.json",
        logs_dir=tmp_path / "logs",
    )
    manager = NetnsProcessManager(
        state_path=tmp_path / "proc.json",
        popen_factory=FakeReadyPopen,
    )
    session = NetnsSessionRunner(harness, manager)
    report = session.run(
        server_cmd=["sh", "-lc", "echo ready"],
        client_cmd=["sh", "-lc", "echo ready"],
        scenario={
            "kind": "tunnel-pair",
            "required_markers": ["call_established"],
            "network_plan": {
                "full_device": True,
                "dns_servers": ["1.1.1.1"],
                "carrier_hosts": ["next-ws.bale.ai"],
                "setup_host_routes": True,
                "client": {
                    "tun": "vpn0",
                    "address": "10.77.0.2/24",
                    "mtu": 1400,
                    "routes": ["0.0.0.0/1"],
                },
                "server": {
                    "tun": "vpn0",
                    "address": "10.77.0.1/24",
                    "mtu": 1400,
                    "wan": "eth0",
                    "enable_nat": True,
                },
            },
        },
        timeout=0.2,
    )
    assert report.ok is False
    assert report.failure_class == "infra"
    assert report.failure_code == "nat_setup_failed"
