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
            stdout.write(b"call_established\ntransport_selected=dc\nproxy_listening=127.0.0.1:1080\nteardown_done\n")
            stdout.flush()


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
    session = NetnsSessionRunner(harness, manager)
    report = session.run(
        server_cmd=["sh", "-lc", "echo ready"],
        client_cmd=["sh", "-lc", "echo ready"],
        scenario={"expected_markers": ["call_established", "transport_selected="]},
        timeout=0.2,
    )
    assert report.ok
    assert report.readiness == "ready"
    assert report.call_established == "yes"
    assert report.transport_selected == "dc"
    assert report.data_flow_ok == "yes"
    assert report.teardown_clean == "yes"
    assert report.artifact_bundle
    assert any(step["phase"] == "smoke" for step in report.harness_steps)
    assert "call_established" in report.log_tails["server"]


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
        scenario={"expected_markers": ["call_established"]},
        timeout=0.1,
    )
    assert not report.ok
    assert report.readiness == "timeout"
    assert report.last_error == "process readiness timeout"
    assert report.failure_class in {"infra_flake", "carrier_instability"}


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
        scenario={"expected_markers": ["call_established"]},
        smoke_commands=[["ip", "netns", "exec", "bb-client", "sh", "-lc", "echo smoke"]],
        timeout=0.2,
    )
    assert report.ok
    assert any(step["phase"] == "smoke-custom" for step in report.harness_steps)


def test_netns_scenario_includes_verdict_markers() -> None:
    scenario = build_proxy_pair_scenario(
        server_jwt_file="/tmp/server.jwt",
        client_jwt_file="/tmp/client.jwt",
        peer_id=7,
        proxy_secret="secret",
    )
    payload = scenario.to_dict()
    assert "expected_markers" in payload
    assert "smoke_kind" in payload
