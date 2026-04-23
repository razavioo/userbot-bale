"""Linux network-namespace planning and execution for single-host VPN tests."""

from __future__ import annotations

import subprocess
import time
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
import shlex

from baleobala.control.observability import (
    StructuredEventRecorder,
    classify_failure,
    environment_snapshot,
    redact_value,
)
from baleobala.control.paths import config_dir, data_dir
from baleobala.control.store import JsonStore


@dataclass(frozen=True)
class NetnsTopology:
    prefix: str = "baleobala"
    client_ns: str = "bb-client"
    server_ns: str = "bb-server"
    client_veth: str = "bb-veth-c"
    server_veth: str = "bb-veth-s"
    client_ip_cidr: str = "172.29.0.1/30"
    server_ip_cidr: str = "172.29.0.2/30"
    client_ip: str = "172.29.0.1"
    server_ip: str = "172.29.0.2"

    @classmethod
    def with_prefix(cls, prefix: str) -> "NetnsTopology":
        trimmed = prefix[:8] if prefix else "baleo"
        return cls(
            prefix=trimmed,
            client_ns=f"{trimmed}-client",
            server_ns=f"{trimmed}-server",
            client_veth=f"{trimmed}-vc",
            server_veth=f"{trimmed}-vs",
        )


Runner = Callable[..., subprocess.CompletedProcess[str]]
PopenFactory = Callable[..., Any]


@dataclass(frozen=True)
class NetnsStepResult:
    phase: str
    command: list[str]
    ok: bool
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "command": self.command,
            "ok": self.ok,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "returncode": self.returncode,
        }


@dataclass(frozen=True)
class NetnsRunReport:
    ok: bool
    stage: str
    topology: dict[str, str]
    steps: list[dict[str, Any]] = field(default_factory=list)
    last_error: str = ""
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": "yes" if self.ok else "no",
            "stage": self.stage,
            "topology": self.topology,
            "steps": self.steps,
            "last_error": self.last_error,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class NetnsProcessSpec:
    namespace: str
    name: str
    command: list[str]
    log_path: str

    def to_dict(self) -> dict[str, str | list[str]]:
        return {
            "namespace": self.namespace,
            "name": self.name,
            "command": self.command,
            "log_path": self.log_path,
        }


@dataclass(frozen=True)
class NetnsProcessStatus:
    name: str
    namespace: str
    pid: int
    running: bool
    command: list[str]
    log_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "namespace": self.namespace,
            "pid": self.pid,
            "running": "yes" if self.running else "no",
            "command": self.command,
            "log_path": self.log_path,
        }


@dataclass(frozen=True)
class NetnsSessionReport:
    ok: bool
    stage: str
    readiness: str
    topology: dict[str, str]
    call_established: str = "no"
    transport_selected: str = ""
    data_flow_ok: str = "no"
    teardown_clean: str = "no"
    tun_created: str = "no"
    routes_programmed: str = "no"
    dns_configured: str = "no"
    nat_configured: str = "no"
    carrier_bypass_configured: str = "no"
    default_route_active: str = "no"
    dns_probe_ok: str = "no"
    egress_probe_ok: str = "no"
    failure_class: str = ""
    failure_code: str = ""
    run_id: str = ""
    last_success_stage: str = ""
    failed_stage: str = ""
    artifact_bundle: str = ""
    process_status: list[dict[str, Any]] = field(default_factory=list)
    harness_steps: list[dict[str, Any]] = field(default_factory=list)
    log_tails: dict[str, str] = field(default_factory=dict)
    last_error: str = ""
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": "yes" if self.ok else "no",
            "stage": self.stage,
            "readiness": self.readiness,
            "call_established": self.call_established,
            "transport_selected": self.transport_selected,
            "data_flow_ok": self.data_flow_ok,
            "teardown_clean": self.teardown_clean,
            "tun_created": self.tun_created,
            "routes_programmed": self.routes_programmed,
            "dns_configured": self.dns_configured,
            "nat_configured": self.nat_configured,
            "carrier_bypass_configured": self.carrier_bypass_configured,
            "default_route_active": self.default_route_active,
            "dns_probe_ok": self.dns_probe_ok,
            "egress_probe_ok": self.egress_probe_ok,
            "failure_class": self.failure_class,
            "failure_code": self.failure_code,
            "run_id": self.run_id,
            "last_success_stage": self.last_success_stage,
            "failed_stage": self.failed_stage,
            "artifact_bundle": self.artifact_bundle,
            "topology": self.topology,
            "process_status": self.process_status,
            "harness_steps": self.harness_steps,
            "log_tails": self.log_tails,
            "last_error": self.last_error,
            "updated_at": self.updated_at,
        }


class NetnsHarness:
    def __init__(
        self,
        topology: NetnsTopology,
        *,
        runner: Runner | None = None,
        state_path: Path | None = None,
        logs_dir: Path | None = None,
    ) -> None:
        self.topology = topology
        self._runner = runner or subprocess.run
        self._store = JsonStore(state_path or (config_dir() / "netns_harness.json"))
        self._logs_dir = logs_dir or (data_dir() / "netns")

    def setup(self) -> NetnsRunReport:
        return self._run_phase("setup", render_setup_commands(self.topology))

    def smoke(self) -> NetnsRunReport:
        return self._run_phase("smoke", render_smoke_commands(self.topology))

    def teardown(self) -> NetnsRunReport:
        return self._run_phase("teardown", render_teardown_commands(self.topology), best_effort=True)

    def full_cycle(self) -> NetnsRunReport:
        setup = self.setup()
        if not setup.ok:
            return setup
        smoke = self.smoke()
        teardown = self.teardown()
        ok = smoke.ok and teardown.ok
        report = NetnsRunReport(
            ok=ok,
            stage="full-cycle",
            topology=_topology_dict(self.topology),
            steps=setup.steps + smoke.steps + teardown.steps,
            last_error=smoke.last_error or teardown.last_error,
        )
        self._store.save(report.to_dict())
        return report

    def build_process_specs(
        self,
        *,
        server_cmd: list[str],
        client_cmd: list[str],
    ) -> list[NetnsProcessSpec]:
        self._logs_dir.mkdir(parents=True, exist_ok=True)
        return [
            NetnsProcessSpec(
                namespace=self.topology.server_ns,
                name="server",
                command=_wrap_netns_exec(self.topology.server_ns, server_cmd),
                log_path=str(self._logs_dir / f"{self.topology.prefix}-server.log"),
            ),
            NetnsProcessSpec(
                namespace=self.topology.client_ns,
                name="client",
                command=_wrap_netns_exec(self.topology.client_ns, client_cmd),
                log_path=str(self._logs_dir / f"{self.topology.prefix}-client.log"),
            ),
        ]

    def status(self) -> NetnsRunReport | None:
        payload = self._store.load(default=None)
        if not isinstance(payload, dict):
            return None
        return NetnsRunReport(
            ok=str(payload.get("ok", "no")) == "yes",
            stage=str(payload.get("stage", "")),
            topology={str(k): str(v) for k, v in payload.get("topology", {}).items()},
            steps=list(payload.get("steps", [])),
            last_error=str(payload.get("last_error", "")),
            updated_at=float(payload.get("updated_at", time.time())),
        )

    def _run_phase(
        self,
        phase: str,
        commands: list[list[str]],
        *,
        best_effort: bool = False,
    ) -> NetnsRunReport:
        steps: list[dict[str, Any]] = []
        last_error = ""
        ok = True
        for cmd in commands:
            try:
                result = self._runner(cmd, check=False, capture_output=True, text=True)
                step = NetnsStepResult(
                    phase=phase,
                    command=list(cmd),
                    ok=result.returncode == 0,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    returncode=result.returncode,
                )
            except OSError as exc:
                step = NetnsStepResult(
                    phase=phase,
                    command=list(cmd),
                    ok=False,
                    stdout="",
                    stderr=str(exc),
                    returncode=127,
                )
            steps.append(step.to_dict())
            if not step.ok and not best_effort:
                ok = False
                last_error = step.stderr.strip() or step.stdout.strip() or f"{phase} failed"
                break
            if not step.ok and best_effort and not last_error:
                last_error = step.stderr.strip() or step.stdout.strip() or f"{phase} had errors"
        report = NetnsRunReport(
            ok=ok if not best_effort else True,
            stage=phase,
            topology=_topology_dict(self.topology),
            steps=steps,
            last_error=last_error,
        )
        self._store.save(report.to_dict())
        return report


def render_setup_commands(topology: NetnsTopology) -> list[list[str]]:
    return [
        ["ip", "netns", "add", topology.client_ns],
        ["ip", "netns", "add", topology.server_ns],
        [
            "ip",
            "link",
            "add",
            topology.client_veth,
            "type",
            "veth",
            "peer",
            "name",
            topology.server_veth,
        ],
        ["ip", "link", "set", topology.client_veth, "netns", topology.client_ns],
        ["ip", "link", "set", topology.server_veth, "netns", topology.server_ns],
        [
            "ip",
            "netns",
            "exec",
            topology.client_ns,
            "ip",
            "addr",
            "add",
            topology.client_ip_cidr,
            "dev",
            topology.client_veth,
        ],
        [
            "ip",
            "netns",
            "exec",
            topology.server_ns,
            "ip",
            "addr",
            "add",
            topology.server_ip_cidr,
            "dev",
            topology.server_veth,
        ],
        ["ip", "netns", "exec", topology.client_ns, "ip", "link", "set", "lo", "up"],
        ["ip", "netns", "exec", topology.server_ns, "ip", "link", "set", "lo", "up"],
        ["ip", "netns", "exec", topology.client_ns, "ip", "link", "set", topology.client_veth, "up"],
        ["ip", "netns", "exec", topology.server_ns, "ip", "link", "set", topology.server_veth, "up"],
    ]


def render_teardown_commands(topology: NetnsTopology) -> list[list[str]]:
    return [
        ["ip", "netns", "del", topology.client_ns],
        ["ip", "netns", "del", topology.server_ns],
    ]


def render_smoke_commands(topology: NetnsTopology) -> list[list[str]]:
    return [
        ["ip", "netns", "exec", topology.client_ns, "ping", "-c", "1", topology.server_ip],
        ["ip", "netns", "exec", topology.server_ns, "ping", "-c", "1", topology.client_ip],
    ]


def render_shell_script(topology: NetnsTopology) -> str:
    lines = ["#!/usr/bin/env bash", "set -euo pipefail", ""]
    lines.append("# setup")
    for cmd in render_setup_commands(topology):
        lines.append(" ".join(cmd))
    lines.append("")
    lines.append("# smoke")
    for cmd in render_smoke_commands(topology):
        lines.append(" ".join(cmd))
    lines.append("")
    lines.append("# teardown")
    for cmd in render_teardown_commands(topology):
        lines.append(" ".join(cmd))
    lines.append("")
    return "\n".join(lines)


def render_process_script(
    topology: NetnsTopology,
    *,
    server_cmd: list[str],
    client_cmd: list[str],
    logs_dir: str,
) -> str:
    lines = [render_shell_script(topology).rstrip(), ""]
    lines.append("# process-launch")
    for spec in _default_process_specs(topology, server_cmd=server_cmd, client_cmd=client_cmd, logs_dir=logs_dir):
        shell_cmd = shlex.join(spec.command)
        lines.append(f"{shell_cmd} > {shlex.quote(spec.log_path)} 2>&1 &")
        lines.append(f'echo "$!" > {shlex.quote(spec.log_path + ".pid")}')
    lines.append("")
    lines.append("# cleanup-processes")
    for spec in _default_process_specs(topology, server_cmd=server_cmd, client_cmd=client_cmd, logs_dir=logs_dir):
        pid_file = spec.log_path + ".pid"
        lines.append(f'if [ -f {shlex.quote(pid_file)} ]; then kill "$(cat {shlex.quote(pid_file)})" 2>/dev/null || true; fi')
    lines.append("")
    return "\n".join(lines)


def _topology_dict(topology: NetnsTopology) -> dict[str, str]:
    return {
        "prefix": topology.prefix,
        "client_ns": topology.client_ns,
        "server_ns": topology.server_ns,
        "client_veth": topology.client_veth,
        "server_veth": topology.server_veth,
        "client_ip_cidr": topology.client_ip_cidr,
        "server_ip_cidr": topology.server_ip_cidr,
        "client_ip": topology.client_ip,
        "server_ip": topology.server_ip,
    }


def _wrap_netns_exec(namespace: str, command: list[str]) -> list[str]:
    return ["ip", "netns", "exec", namespace, *command]


def _default_process_specs(
    topology: NetnsTopology,
    *,
    server_cmd: list[str],
    client_cmd: list[str],
    logs_dir: str,
) -> list[NetnsProcessSpec]:
    return [
        NetnsProcessSpec(
            namespace=topology.server_ns,
            name="server",
            command=_wrap_netns_exec(topology.server_ns, server_cmd),
            log_path=str(Path(logs_dir) / f"{topology.prefix}-server.log"),
        ),
        NetnsProcessSpec(
            namespace=topology.client_ns,
            name="client",
            command=_wrap_netns_exec(topology.client_ns, client_cmd),
            log_path=str(Path(logs_dir) / f"{topology.prefix}-client.log"),
        ),
    ]


class NetnsProcessManager:
    def __init__(
        self,
        *,
        state_path: Path | None = None,
        popen_factory: PopenFactory | None = None,
    ) -> None:
        self._store = JsonStore(state_path or (config_dir() / "netns_processes.json"))
        self._popen_factory = popen_factory or subprocess.Popen
        self._children: dict[str, Any] = {}

    def start(self, specs: list[NetnsProcessSpec]) -> list[NetnsProcessStatus]:
        statuses: list[NetnsProcessStatus] = []
        payload: dict[str, Any] = {"items": []}
        for spec in specs:
            log_path = Path(spec.log_path)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handle = log_path.open("ab")
            try:
                proc = self._popen_factory(
                    spec.command,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                )
            finally:
                handle.close()
            self._children[spec.name] = proc
            status = NetnsProcessStatus(
                name=spec.name,
                namespace=spec.namespace,
                pid=int(proc.pid),
                running=proc.poll() is None,
                command=spec.command,
                log_path=spec.log_path,
            )
            statuses.append(status)
            payload["items"].append(status.to_dict())
        self._store.save(payload)
        return statuses

    def stop(self) -> list[NetnsProcessStatus]:
        statuses = self.status()
        for item in statuses:
            proc = self._children.get(item.name)
            if proc is None:
                continue
            try:
                proc.terminate()
            except Exception:
                pass
        final_statuses = self.status()
        self._store.save({"items": [item.to_dict() for item in final_statuses]})
        return final_statuses

    def status(self) -> list[NetnsProcessStatus]:
        payload = self._store.load(default={"items": []})
        items = payload.get("items", []) if isinstance(payload, dict) else []
        statuses: list[NetnsProcessStatus] = []
        for item in items:
            name = str(item.get("name", ""))
            proc = self._children.get(name)
            if proc is not None:
                running = proc.poll() is None
                pid = int(proc.pid)
            else:
                running = str(item.get("running", "no")) == "yes"
                pid = int(item.get("pid", 0))
            statuses.append(
                NetnsProcessStatus(
                    name=name,
                    namespace=str(item.get("namespace", "")),
                    pid=pid,
                    running=running,
                    command=[str(part) for part in item.get("command", [])],
                    log_path=str(item.get("log_path", "")),
                )
            )
        return statuses


def _log_contains(path: str, needle: str) -> bool:
    try:
        return needle in Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def _collect_log_tails(statuses: list[NetnsProcessStatus], *, max_chars: int = 400) -> dict[str, str]:
    tails: dict[str, str] = {}
    for item in statuses:
        try:
            text = Path(item.log_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            tails[item.name] = ""
            continue
        tails[item.name] = text[-max_chars:]
    return tails


def _contains_marker(text: str, marker: str) -> bool:
    return marker in text


def _parse_marker_lines(text: str) -> list[str]:
    exact = {"call_established", "teardown_done"}
    prefix = ("transport_selected=", "proxy_listening=", "tunnel_up=")
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in exact or any(stripped.startswith(item) for item in prefix):
            out.append(stripped)
    return sorted(set(out))


def _marker_value(markers: list[str], prefix: str) -> str:
    for marker in markers:
        if marker.startswith(prefix):
            return marker[len(prefix) :].strip()
    return ""


def _has_marker(markers: list[str], marker: str) -> bool:
    return any(item == marker or item.startswith(marker) for item in markers)


def _first_failed_step(report: NetnsRunReport) -> dict[str, Any]:
    for step in report.steps:
        if not bool(step.get("ok", False)):
            return step
    return {}


def _classify_network_failure(report: NetnsRunReport) -> str:
    step = _first_failed_step(report)
    command = " ".join(str(part) for part in step.get("command", []))
    if "iptables" in command:
        return "nat_setup_failed"
    if "/etc/netns/" in command or "resolv.conf" in command:
        return "dns_config_failed"
    if (
        "ip route" in command
        and " via " in f" {command} "
        and " default " not in f" {command} "
        and "/1" not in command
    ):
        return "host_route_failed"
    if "ip route" in command:
        return "route_program_failed"
    if "tuntap" in command or "link del" in command:
        return "missing_tun"
    return ""


def _snapshot_commands(topology: NetnsTopology) -> dict[str, list[str]]:
    return {
        "client_routes": ["ip", "netns", "exec", topology.client_ns, "ip", "route", "show"],
        "server_routes": ["ip", "netns", "exec", topology.server_ns, "ip", "route", "show"],
        "client_dns": ["sh", "-lc", f"cat /etc/netns/{shlex.quote(topology.client_ns)}/resolv.conf 2>/dev/null || true"],
        "server_dns": ["sh", "-lc", f"cat /etc/netns/{shlex.quote(topology.server_ns)}/resolv.conf 2>/dev/null || true"],
    }


def _step_payload(item: Any) -> dict[str, Any]:
    if hasattr(item, "to_dict"):
        return dict(item.to_dict())
    if isinstance(item, dict):
        return dict(item)
    return {"value": item}


class NetnsSessionRunner:
    def __init__(self, harness: NetnsHarness, process_manager: NetnsProcessManager, *, artifact_root: Path | None = None) -> None:
        self._harness = harness
        self._process_manager = process_manager
        self._artifact_root = artifact_root or (data_dir() / "netns-artifacts")

    def run(
        self,
        *,
        server_cmd: list[str],
        client_cmd: list[str],
        server_ready_pattern: str = "",
        client_ready_pattern: str = "",
        smoke_commands: list[list[str]] | None = None,
        timeout: float = 5.0,
        scenario: dict[str, Any] | None = None,
    ) -> NetnsSessionReport:
        bundle_dir = self._make_artifact_bundle()
        scenario = scenario or {}
        recorder = StructuredEventRecorder(
            component="netns-session",
            base_fields={
                "backend": str(scenario.get("kind", "")),
                "artifact_bundle": str(bundle_dir),
            },
        )
        required_markers = [str(marker) for marker in scenario.get("required_markers", []) if marker]
        network_plan = scenario.get("network_plan", {})
        recorder.event("session_run_started", stage="preflight", outcome="begin", transport_requested=str(scenario.get("transport", "")), scenario=redact_value(scenario))
        preflight = self._preflight(scenario)
        if not preflight.ok:
            self._write_bundle(
                bundle_dir,
                scenario=scenario,
                setup=preflight,
                smoke=NetnsRunReport(False, "smoke-skipped", _topology_dict(self._harness.topology)),
                teardown=NetnsRunReport(False, "teardown-skipped", _topology_dict(self._harness.topology)),
                started=[],
                stopped=[],
                markers=[],
                failure_class=preflight.last_error or "infra",
                failure_code=preflight.last_error or "preflight_failed",
                recorder=recorder,
            )
            return self._report(ok=False, stage="preflight", readiness="failed", failure_class="infra", failure_code=preflight.last_error or "preflight_failed", run_id=recorder.run_id, last_success_stage=recorder.last_success_stage, failed_stage="preflight", artifact_bundle=str(bundle_dir), last_error=preflight.last_error)
        setup = self._harness.setup()
        if not setup.ok:
            self._write_bundle(bundle_dir, scenario=scenario, setup=setup, smoke=NetnsRunReport(False, "smoke-skipped", _topology_dict(self._harness.topology)), teardown=NetnsRunReport(False, "teardown-skipped", _topology_dict(self._harness.topology)), started=[], stopped=[], markers=[], failure_class="infra", failure_code=setup.last_error or "setup_failed", recorder=recorder)
            return self._report(ok=False, stage="setup", readiness="failed", harness_steps=setup.steps, last_error=setup.last_error, failure_class="infra", failure_code=setup.last_error or "setup_failed", run_id=recorder.run_id, last_success_stage=recorder.last_success_stage, failed_stage="setup", artifact_bundle=str(bundle_dir))
        recorder.event(
            "setup_completed",
            stage="setup",
            outcome="success",
        )
        network_setup = self._network_setup(network_plan)
        network = self._run_network_phase(network_setup["setup_commands"], phase="runtime-setup")
        if not network.ok:
            failure = classify_failure(
                ready=False,
                smoke_ok=False,
                setup_ok=True,
                teardown_ok=False,
                last_error=network.last_error,
                bundle_ok=False,
                legacy_failure_class=_classify_network_failure(network) or "runtime_setup_failed",
            )
            self._write_bundle(bundle_dir, scenario=scenario, setup=setup, smoke=NetnsRunReport(False, "smoke-skipped", _topology_dict(self._harness.topology)), teardown=NetnsRunReport(False, "teardown-skipped", _topology_dict(self._harness.topology)), started=[], stopped=[], markers=[], failure_class="infra", failure_code=failure.failure_code, recorder=recorder, network=network.to_dict())
            return self._report(ok=False, stage="runtime_setup", readiness="failed", tun_created=network_setup["tun_created"], routes_programmed=network_setup["routes_programmed"], dns_configured=network_setup["dns_configured"], nat_configured=network_setup["nat_configured"], carrier_bypass_configured=network_setup["carrier_bypass_configured"], default_route_active=network_setup["default_route_active"], failure_class="infra", failure_code=failure.failure_code, run_id=recorder.run_id, last_success_stage=recorder.last_success_stage, failed_stage="runtime_setup", artifact_bundle=str(bundle_dir), harness_steps=setup.steps + network.steps, last_error=network.last_error)

        started = self._process_manager.start(
            self._harness.build_process_specs(server_cmd=server_cmd, client_cmd=client_cmd)
        )
        recorder.event("processes_started", stage="process_start", outcome="begin", process_status=[item.to_dict() for item in started])
        ready, ready_error, markers = self._wait_for_ready(
            started,
            server_ready_pattern=server_ready_pattern,
            client_ready_pattern=client_ready_pattern,
            timeout=timeout,
            required_markers=required_markers,
            recorder=recorder,
        )
        if ready:
            recorder.event("smoke_started", stage="smoke", outcome="begin", smoke_mode=str(scenario.get("smoke_kind", "")))
            smoke = self._harness._run_phase("smoke", smoke_commands or [])
        else:
            smoke = NetnsRunReport(
                ok=False,
                stage="smoke-skipped",
                topology=_topology_dict(self._harness.topology),
                steps=[],
                last_error=ready_error,
            )
        if ready:
            recorder.event("smoke_completed" if smoke.ok else "smoke_failed", stage="smoke", outcome="success" if smoke.ok else "failure", error_message="" if smoke.ok else smoke.last_error)
        stopped = self._process_manager.stop()
        recorder.event("processes_stopped", stage="process_stop", outcome="begin", process_status=[item.to_dict() for item in stopped])
        network_teardown = self._run_network_phase(network_setup["teardown_commands"], phase="teardown", best_effort=True)
        teardown = self._harness.teardown()
        recorder.event("teardown_completed" if teardown.ok else "teardown_failed", stage="teardown", outcome="success" if teardown.ok else "failure", error_message="" if teardown.ok else teardown.last_error)
        ok = ready and smoke.ok and teardown.ok and network_teardown.ok
        if not ready:
            recorder.event("readiness_failed", stage="readiness_wait", outcome="failure", error_message=ready_error, failure_class="call_setup", failure_code="call_setup_timeout")
        elif _has_marker(markers, "call_established"):
            recorder.event("call_established", stage="readiness_wait", outcome="success", transport_selected=_marker_value(markers, "transport_selected="))
        bundle_ok = self._write_bundle(
            bundle_dir,
            scenario=scenario,
            setup=setup,
            smoke=smoke,
            teardown=teardown,
            started=started,
            stopped=stopped,
            markers=markers,
            failure_class="" if ok else (_classify_network_failure(network) or _classify_network_failure(network_teardown) or "product_bug"),
            failure_code=ready_error or smoke.last_error or network.last_error or network_teardown.last_error or teardown.last_error,
            recorder=recorder,
            network={
                "setup": network.to_dict(),
                "teardown": network_teardown.to_dict(),
            },
        )
        log_tails = _collect_log_tails(stopped)
        final_markers = self._collect_markers(stopped)
        if final_markers:
            markers = final_markers
        call_established = "yes" if _has_marker(markers, "call_established") else "no"
        transport_selected = _marker_value(markers, "transport_selected=")
        data_flow_ok = "yes" if smoke.ok and ready else "no"
        teardown_clean = "yes" if teardown.ok and network_teardown.ok else "no"
        from baleobala.control.analyzer import analyze_bundle
        final_failure = classify_failure(ready=ready, smoke_ok=smoke.ok, setup_ok=setup.ok, teardown_ok=teardown.ok and network_teardown.ok, last_error=ready_error or smoke.last_error or network.last_error or network_teardown.last_error or teardown.last_error, bundle_ok=bundle_ok, markers=markers, legacy_failure_class=_classify_network_failure(network) or _classify_network_failure(network_teardown))
        bundle_analysis = analyze_bundle(bundle_dir)
        return self._report(
            ok=ok,
            stage="session",
            readiness="ready" if ready else "timeout",
            call_established=call_established,
            transport_selected=transport_selected,
            data_flow_ok=data_flow_ok,
            teardown_clean=teardown_clean,
            tun_created=network_setup["tun_created"],
            routes_programmed=network_setup["routes_programmed"],
            dns_configured=network_setup["dns_configured"],
            nat_configured=network_setup["nat_configured"],
            carrier_bypass_configured=network_setup["carrier_bypass_configured"],
            default_route_active=network_setup["default_route_active"],
            dns_probe_ok="yes" if self._probe_enabled(network_plan, "dns_servers") and smoke.ok else "no",
            egress_probe_ok="yes" if bool(network_plan.get("full_device")) and smoke.ok else "no",
            failure_class=final_failure.failure_class,
            failure_code=final_failure.failure_code,
            run_id=recorder.run_id,
            last_success_stage=recorder.last_success_stage,
            failed_stage=recorder.failed_stage,
            artifact_bundle=str(bundle_dir),
            process_status=[item.to_dict() for item in stopped],
            harness_steps=setup.steps
            + network.steps
            + ([{"phase": smoke.stage, "ok": smoke.ok, "last_error": smoke.last_error}] if smoke.stage else [])
            + smoke.steps
            + network_teardown.steps
            + teardown.steps,
            log_tails=log_tails,
            last_error="" if ok else (ready_error or smoke.last_error or network.last_error or network_teardown.last_error or teardown.last_error),
        )

    def _probe_enabled(self, network_plan: dict[str, Any], field: str) -> bool:
        value = network_plan.get(field)
        if isinstance(value, (list, tuple)):
            return bool(value)
        return bool(value)

    def _preflight(self, scenario: dict[str, Any]) -> NetnsRunReport:
        checks = [
            (["ip", "-V"], "missing_iproute"),
            (["ping", "-V"], "missing_ping"),
        ]
        if scenario.get("kind") == "tunnel-pair":
            checks.append((["sh", "-lc", "test -e /dev/net/tun"], "missing_tun"))
        for cmd, code in checks:
            try:
                result = self._harness._runner(cmd, check=False, capture_output=True, text=True)
            except OSError as exc:
                return NetnsRunReport(False, "preflight", _topology_dict(self._harness.topology), last_error=code if code != "missing_ping" else str(exc))
            if result.returncode != 0:
                return NetnsRunReport(False, "preflight", _topology_dict(self._harness.topology), last_error=code)
        return NetnsRunReport(True, "preflight", _topology_dict(self._harness.topology))

    def _run_network_phase(
        self,
        commands: list[list[str]],
        *,
        phase: str,
        best_effort: bool = False,
    ) -> NetnsRunReport:
        if not commands:
            return NetnsRunReport(ok=True, stage=phase, topology=_topology_dict(self._harness.topology), steps=[])
        return self._harness._run_phase(phase, commands, best_effort=best_effort)

    def _network_setup(self, network_plan: Any) -> dict[str, Any]:
        if not isinstance(network_plan, dict) or not network_plan:
            return {
                "setup_commands": [],
                "teardown_commands": [],
                "tun_created": "no",
                "routes_programmed": "no",
                "dns_configured": "no",
                "nat_configured": "no",
                "carrier_bypass_configured": "no",
                "default_route_active": "no",
            }
        topology = self._harness.topology
        client = network_plan.get("client", {}) if isinstance(network_plan.get("client", {}), dict) else {}
        server = network_plan.get("server", {}) if isinstance(network_plan.get("server", {}), dict) else {}
        full_device = bool(network_plan.get("full_device", False))
        dns_servers = [str(item) for item in network_plan.get("dns_servers", [])]
        carrier_hosts = [str(item) for item in network_plan.get("carrier_hosts", [])]
        setup_host_routes = bool(network_plan.get("setup_host_routes", False))
        client_tun = str(client.get("tun", "vpn0"))
        client_addr = str(client.get("address", "10.77.0.2/24"))
        client_mtu = str(client.get("mtu", 1400))
        client_routes = [str(item) for item in client.get("routes", [])]
        server_tun = str(server.get("tun", "vpn0"))
        server_addr = str(server.get("address", "10.77.0.1/24"))
        server_mtu = str(server.get("mtu", 1400))
        wan = str(server.get("wan", "eth0"))
        enable_nat = bool(server.get("enable_nat", False))
        dns_dir = f"/etc/netns/{topology.client_ns}"
        dns_file = f"{dns_dir}/resolv.conf"
        dns_payload = "".join(f"nameserver {item}\\n" for item in dns_servers)

        setup_commands: list[list[str]] = [
            ["ip", "netns", "exec", topology.client_ns, "ip", "tuntap", "add", "dev", client_tun, "mode", "tun"],
            ["ip", "netns", "exec", topology.client_ns, "ip", "addr", "add", client_addr, "dev", client_tun],
            ["ip", "netns", "exec", topology.client_ns, "ip", "link", "set", client_tun, "mtu", client_mtu, "up"],
            ["ip", "netns", "exec", topology.server_ns, "ip", "tuntap", "add", "dev", server_tun, "mode", "tun"],
            ["ip", "netns", "exec", topology.server_ns, "ip", "addr", "add", server_addr, "dev", server_tun],
            ["ip", "netns", "exec", topology.server_ns, "ip", "link", "set", server_tun, "mtu", server_mtu, "up"],
        ]
        if setup_host_routes:
            for host in carrier_hosts:
                setup_commands.append(
                    [
                        "ip",
                        "netns",
                        "exec",
                        topology.client_ns,
                        "ip",
                        "route",
                        "replace",
                        host,
                        "via",
                        topology.server_ip,
                        "dev",
                        topology.client_veth,
                    ]
                )
        for route in client_routes:
            setup_commands.append(["ip", "netns", "exec", topology.client_ns, "ip", "route", "add", route, "dev", client_tun])
        if full_device:
            setup_commands.append(["ip", "netns", "exec", topology.client_ns, "ip", "route", "replace", "default", "via", "10.77.0.1", "dev", client_tun, "metric", "50"])
        if dns_servers:
            setup_commands.append(
                [
                    "sh",
                    "-lc",
                    f"mkdir -p {shlex.quote(dns_dir)} && printf %s {shlex.quote(dns_payload)} > {shlex.quote(dns_file)}",
                ]
            )
        if enable_nat:
            setup_commands.extend(
                [
                    ["ip", "netns", "exec", topology.server_ns, "sysctl", "-w", "net.ipv4.ip_forward=1"],
                    ["ip", "netns", "exec", topology.server_ns, "iptables", "-t", "nat", "-A", "POSTROUTING", "-o", wan, "-j", "MASQUERADE"],
                    ["ip", "netns", "exec", topology.server_ns, "iptables", "-A", "FORWARD", "-i", server_tun, "-o", wan, "-j", "ACCEPT"],
                    ["ip", "netns", "exec", topology.server_ns, "iptables", "-A", "FORWARD", "-i", wan, "-o", server_tun, "-m", "state", "--state", "RELATED,ESTABLISHED", "-j", "ACCEPT"],
                ]
            )
        teardown_commands: list[list[str]] = []
        for host in carrier_hosts:
            if setup_host_routes:
                teardown_commands.append(
                    [
                        "ip",
                        "netns",
                        "exec",
                        topology.client_ns,
                        "ip",
                        "route",
                        "del",
                        host,
                        "via",
                        topology.server_ip,
                        "dev",
                        topology.client_veth,
                    ]
                )
        if enable_nat:
            teardown_commands.extend(
                [
                    ["ip", "netns", "exec", topology.server_ns, "iptables", "-t", "nat", "-D", "POSTROUTING", "-o", wan, "-j", "MASQUERADE"],
                    ["ip", "netns", "exec", topology.server_ns, "iptables", "-D", "FORWARD", "-i", server_tun, "-o", wan, "-j", "ACCEPT"],
                    ["ip", "netns", "exec", topology.server_ns, "iptables", "-D", "FORWARD", "-i", wan, "-o", server_tun, "-m", "state", "--state", "RELATED,ESTABLISHED", "-j", "ACCEPT"],
                ]
            )
        if dns_servers:
            teardown_commands.append(["sh", "-lc", f"rm -f {shlex.quote(dns_file)} && rmdir {shlex.quote(dns_dir)} 2>/dev/null || true"])
        teardown_commands.extend(
            [
                ["ip", "netns", "exec", topology.client_ns, "ip", "link", "del", client_tun],
                ["ip", "netns", "exec", topology.server_ns, "ip", "link", "del", server_tun],
            ]
        )
        return {
            "setup_commands": setup_commands,
            "teardown_commands": teardown_commands,
            "tun_created": "yes",
            "routes_programmed": "yes" if client_routes or full_device else "no",
            "dns_configured": "yes" if dns_servers else "no",
            "nat_configured": "yes" if enable_nat else "no",
            "carrier_bypass_configured": "yes" if setup_host_routes and carrier_hosts else "no",
            "default_route_active": "yes" if full_device else "no",
        }

    def _wait_for_ready(
        self,
        statuses: list[NetnsProcessStatus],
        *,
        server_ready_pattern: str,
        client_ready_pattern: str,
        timeout: float,
        required_markers: list[str],
        recorder: StructuredEventRecorder | None = None,
    ) -> tuple[bool, str, list[str]]:
        deadline = time.time() + timeout
        markers: list[str] = []
        while time.time() < deadline:
            markers = self._collect_markers(statuses)
            transport_selected = _marker_value(markers, "transport_selected=")
            if recorder is not None and markers:
                recorder.event(
                    "readiness_markers_observed",
                    stage="call_setup",
                    outcome="begin",
                    markers=markers,
                    transport_selected=transport_selected or None,
                )
            if required_markers and not self._required_markers_ready(statuses, required_markers=required_markers):
                time.sleep(0.05)
                continue
            if self._patterns_ready(
                statuses,
                server_ready_pattern=server_ready_pattern,
                client_ready_pattern=client_ready_pattern,
            ):
                if recorder is not None:
                    recorder.event(
                        "readiness_satisfied",
                        stage="call_setup",
                        outcome="success",
                        markers=markers,
                        transport_selected=transport_selected or None,
                    )
                return True, "", markers
            time.sleep(0.05)
        if recorder is not None:
            recorder.event(
                "readiness_timeout",
                stage="call_setup",
                outcome="failure",
                error_message="process readiness timeout",
                markers=markers,
            )
        return False, "process readiness timeout", markers

    def _required_markers_ready(self, statuses: list[NetnsProcessStatus], *, required_markers: list[str]) -> bool:
        if not required_markers:
            return True
        log_text = ""
        for item in statuses:
            try:
                log_text += "\n" + Path(item.log_path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
        return all(marker in log_text for marker in required_markers)

    def _collect_markers(self, statuses: list[NetnsProcessStatus]) -> list[str]:
        out: list[str] = []
        for item in statuses:
            try:
                text = Path(item.log_path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            out.extend(_parse_marker_lines(text))
        return sorted(set(out))

    def _patterns_ready(
        self,
        statuses: list[NetnsProcessStatus],
        *,
        server_ready_pattern: str,
        client_ready_pattern: str,
    ) -> bool:
        by_name = {item.name: item for item in statuses}
        server_ok = _log_contains(by_name["server"].log_path, server_ready_pattern) if server_ready_pattern else True
        client_ok = _log_contains(by_name["client"].log_path, client_ready_pattern) if client_ready_pattern else True
        return server_ok and client_ok

    def _report(
        self,
        *,
        ok: bool,
        stage: str,
        readiness: str,
        call_established: str = "no",
        transport_selected: str = "",
        data_flow_ok: str = "no",
        teardown_clean: str = "no",
        tun_created: str = "no",
        routes_programmed: str = "no",
        dns_configured: str = "no",
        nat_configured: str = "no",
        carrier_bypass_configured: str = "no",
        default_route_active: str = "no",
        dns_probe_ok: str = "no",
        egress_probe_ok: str = "no",
        failure_class: str = "",
        failure_code: str = "",
        run_id: str = "",
        last_success_stage: str = "",
        failed_stage: str = "",
        artifact_bundle: str = "",
        process_status: list[dict[str, Any]] | None = None,
        harness_steps: list[dict[str, Any]] | None = None,
        log_tails: dict[str, str] | None = None,
        last_error: str = "",
    ) -> NetnsSessionReport:
        return NetnsSessionReport(
            ok=ok,
            stage=stage,
            readiness=readiness,
            call_established=call_established,
            transport_selected=transport_selected,
            data_flow_ok=data_flow_ok,
            teardown_clean=teardown_clean,
            tun_created=tun_created,
            routes_programmed=routes_programmed,
            dns_configured=dns_configured,
            nat_configured=nat_configured,
            carrier_bypass_configured=carrier_bypass_configured,
            default_route_active=default_route_active,
            dns_probe_ok=dns_probe_ok,
            egress_probe_ok=egress_probe_ok,
            failure_class=failure_class,
            failure_code=failure_code,
            run_id=run_id,
            last_success_stage=last_success_stage,
            failed_stage=failed_stage,
            artifact_bundle=artifact_bundle,
            topology=_topology_dict(self._harness.topology),
            process_status=process_status or [],
            harness_steps=harness_steps or [],
            log_tails=log_tails or {},
            last_error=last_error,
        )

    def _make_artifact_bundle(self) -> Path:
        root = self._artifact_root
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError:
            root = Path(tempfile.gettempdir()) / "baleobala-netns-artifacts"
            root.mkdir(parents=True, exist_ok=True)
        bundle_dir = root / f"{self._harness.topology.prefix}-{int(time.time() * 1000)}"
        try:
            bundle_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            root = Path(tempfile.gettempdir()) / "baleobala-netns-artifacts"
            root.mkdir(parents=True, exist_ok=True)
            bundle_dir = root / f"{self._harness.topology.prefix}-{int(time.time() * 1000)}"
            bundle_dir.mkdir(parents=True, exist_ok=True)
        return bundle_dir

    def _capture_snapshot(self) -> dict[str, Any]:
        snapshot: dict[str, Any] = {"routes": {}, "dns": {}}
        commands = _snapshot_commands(self._harness.topology)
        for name, cmd in commands.items():
            try:
                result = self._harness._runner(cmd, check=False, capture_output=True, text=True)
                payload = {
                    "ok": "yes" if result.returncode == 0 else "no",
                    "command": list(cmd),
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "returncode": int(result.returncode),
                }
            except OSError as exc:
                payload = {
                    "ok": "no",
                    "command": list(cmd),
                    "stdout": "",
                    "stderr": str(exc),
                    "returncode": 127,
                }
            if name.endswith("_routes"):
                snapshot["routes"][name.removesuffix("_routes")] = payload
            else:
                snapshot["dns"][name.removesuffix("_dns")] = payload
        return snapshot

    def _write_bundle(
        self,
        bundle_dir: Path,
        *,
        scenario: dict[str, Any] | None,
        setup: NetnsRunReport,
        smoke: NetnsRunReport,
        teardown: NetnsRunReport,
        started: list[NetnsProcessStatus],
        stopped: list[NetnsProcessStatus],
        markers: list[str],
        failure_class: str,
        failure_code: str,
        recorder: StructuredEventRecorder,
        network: dict[str, Any] | None = None,
    ) -> bool:
        try:
            log_tails = _collect_log_tails(stopped)
            scenario_payload = redact_value(scenario or {})
            snapshot = self._capture_snapshot()
            summary = {
                "run_id": recorder.run_id,
                "failure_class": failure_class,
                "failure_code": failure_code,
                "last_success_stage": recorder.last_success_stage,
                "failed_stage": recorder.failed_stage,
                "transport_selected": _marker_value(markers, "transport_selected="),
                "call_established": "yes" if _has_marker(markers, "call_established") else "no",
                "smoke_kind": str(scenario_payload.get("smoke_kind", "")) if isinstance(scenario_payload, dict) else "",
                "artifact_bundle": str(bundle_dir),
            }
            payload = {
                "topology": _topology_dict(self._harness.topology),
                "scenario": scenario_payload,
                "setup": setup.to_dict(),
                "smoke": smoke.to_dict(),
                "teardown": teardown.to_dict(),
                "started": [item.to_dict() for item in started],
                "stopped": [item.to_dict() for item in stopped],
                "markers": markers,
                "network": network or {},
                "failure_class": failure_class,
                "failure_code": failure_code,
                "run_id": recorder.run_id,
                "last_success_stage": recorder.last_success_stage,
                "failed_stage": recorder.failed_stage,
                "log_tails": log_tails,
                "snapshots": snapshot,
            }
            (bundle_dir / "verdict.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            (bundle_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
            (bundle_dir / "scenario.json").write_text(json.dumps(scenario_payload, indent=2, sort_keys=True), encoding="utf-8")
            (bundle_dir / "environment.json").write_text(json.dumps(environment_snapshot(), indent=2, sort_keys=True), encoding="utf-8")
            (bundle_dir / "process_status.json").write_text(
                json.dumps(
                    {
                        "started": [item.to_dict() for item in started],
                        "stopped": [item.to_dict() for item in stopped],
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            (bundle_dir / "smoke.json").write_text(json.dumps(smoke.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
            (bundle_dir / "teardown.json").write_text(json.dumps(teardown.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
            (bundle_dir / "markers.json").write_text(json.dumps({"markers": markers}, indent=2, sort_keys=True), encoding="utf-8")
            (bundle_dir / "network.json").write_text(json.dumps(network or {}, indent=2, sort_keys=True), encoding="utf-8")
            (bundle_dir / "route_snapshot.json").write_text(json.dumps(snapshot.get("routes", {}), indent=2, sort_keys=True), encoding="utf-8")
            (bundle_dir / "dns_snapshot.json").write_text(json.dumps(snapshot.get("dns", {}), indent=2, sort_keys=True), encoding="utf-8")
            (bundle_dir / "command_transcript.json").write_text(
                json.dumps(
                    {
                        "setup": [_step_payload(step) for step in setup.steps],
                        "smoke": [_step_payload(step) for step in smoke.steps],
                        "teardown": [_step_payload(step) for step in teardown.steps],
                        "started": [item.to_dict() for item in started],
                        "stopped": [item.to_dict() for item in stopped],
                        "markers": markers,
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            recorder.write_jsonl(bundle_dir / "events.jsonl")
            for item in started:
                src = Path(item.log_path)
                if src.exists():
                    name = "server.log" if item.name == "server" else "client.log" if item.name == "client" else f"{item.name}.log"
                    (bundle_dir / name).write_text(src.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
            return True
        except OSError:
            return False
