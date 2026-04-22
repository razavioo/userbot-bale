"""Linux network-namespace planning and execution for single-host VPN tests."""

from __future__ import annotations

import subprocess
import time
import json
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


def _marker_value(markers: list[str], prefix: str) -> str:
    for marker in markers:
        if marker.startswith(prefix):
            return marker[len(prefix) :].strip()
    return ""


def _has_marker(markers: list[str], marker: str) -> bool:
    return any(item == marker or item.startswith(marker) for item in markers)


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
        recorder = StructuredEventRecorder(
            component="netns-session",
            base_fields={
                "backend": str((scenario or {}).get("kind", "")),
                "artifact_bundle": str(bundle_dir),
            },
        )
        required_markers = [str(marker) for marker in (scenario or {}).get("expected_markers", []) if marker]
        recorder.event(
            "session_run_started",
            stage="setup",
            outcome="begin",
            transport_requested=str((scenario or {}).get("transport", "")),
            scenario=redact_value(scenario or {}),
        )
        setup = self._harness.setup()
        if not setup.ok:
            failure = classify_failure(
                ready=False,
                smoke_ok=False,
                setup_ok=False,
                teardown_ok=False,
                last_error=setup.last_error,
                bundle_ok=False,
            )
            recorder.event(
                "setup_failed",
                stage="setup",
                outcome="failure",
                error_message=setup.last_error,
                failure_class=failure.failure_class,
                failure_code=failure.failure_code,
            )
            self._write_bundle(
                bundle_dir,
                scenario=scenario,
                setup=setup,
                smoke=NetnsRunReport(False, "smoke-skipped", _topology_dict(self._harness.topology)),
                teardown=NetnsRunReport(False, "teardown-skipped", _topology_dict(self._harness.topology)),
                started=[],
                stopped=[],
                markers=[],
                failure_class=failure.failure_class,
                failure_code=failure.failure_code,
                recorder=recorder,
            )
            return self._report(
                ok=False,
                stage="setup",
                readiness="failed",
                harness_steps=setup.steps,
                last_error=setup.last_error,
                failure_class=failure.failure_class,
                failure_code=failure.failure_code,
                run_id=recorder.run_id,
                last_success_stage=recorder.last_success_stage,
                failed_stage=recorder.failed_stage or "setup",
                artifact_bundle=str(bundle_dir),
            )
        recorder.event("setup_completed", stage="setup", outcome="success")

        started = self._process_manager.start(
            self._harness.build_process_specs(server_cmd=server_cmd, client_cmd=client_cmd)
        )
        recorder.event(
            "processes_started",
            stage="call_setup",
            outcome="begin",
            process_status=[item.to_dict() for item in started],
        )
        ready, ready_error, markers = self._wait_for_ready(
            started,
            server_ready_pattern=server_ready_pattern,
            client_ready_pattern=client_ready_pattern,
            timeout=timeout,
            required_markers=required_markers,
            recorder=recorder,
        )
        if ready and smoke_commands:
            recorder.event("smoke_started", stage="smoke", outcome="begin", smoke_mode="custom")
            smoke = self._harness._run_phase("smoke-custom", smoke_commands)
        elif ready:
            recorder.event("smoke_started", stage="smoke", outcome="begin", smoke_mode="default")
            smoke = self._harness.smoke()
        else:
            smoke = NetnsRunReport(
                ok=False,
                stage="smoke-skipped",
                topology=_topology_dict(self._harness.topology),
                steps=[],
                last_error=ready_error,
            )
        if ready:
            recorder.event(
                "smoke_completed" if smoke.ok else "smoke_failed",
                stage="smoke",
                outcome="success" if smoke.ok else "failure",
                error_message="" if smoke.ok else smoke.last_error,
            )
        stopped = self._process_manager.stop()
        recorder.event(
            "processes_stopped",
            stage="teardown",
            outcome="begin",
            process_status=[item.to_dict() for item in stopped],
        )
        teardown = self._harness.teardown()
        recorder.event(
            "teardown_completed" if teardown.ok else "teardown_failed",
            stage="teardown",
            outcome="success" if teardown.ok else "failure",
            error_message="" if teardown.ok else teardown.last_error,
        )
        ok = ready and smoke.ok and teardown.ok
        failure = classify_failure(
            ready=ready,
            smoke_ok=smoke.ok,
            setup_ok=setup.ok,
            teardown_ok=teardown.ok,
            last_error=ready_error or smoke.last_error or teardown.last_error,
            bundle_ok=True,
            markers=markers,
        )
        if not ready:
            recorder.event(
                "readiness_failed",
                stage="call_setup",
                outcome="failure",
                error_message=ready_error,
                failure_class=failure.failure_class,
                failure_code=failure.failure_code,
            )
        elif _has_marker(markers, "call_established"):
            recorder.event(
                "call_established",
                stage="call_setup",
                outcome="success",
                transport_selected=_marker_value(markers, "transport_selected="),
            )
        bundle_ok = self._write_bundle(
            bundle_dir,
            scenario=scenario,
            setup=setup,
            smoke=smoke,
            teardown=teardown,
            started=started,
            stopped=stopped,
            markers=markers,
            failure_class=failure.failure_class,
            failure_code=failure.failure_code,
            recorder=recorder,
        )
        log_tails = _collect_log_tails(stopped)
        final_markers = self._collect_markers(stopped)
        if final_markers:
            markers = final_markers
        call_established = "yes" if _has_marker(markers, "call_established") else "no"
        transport_selected = _marker_value(markers, "transport_selected=")
        data_flow_ok = "yes" if smoke.ok else "no"
        teardown_clean = "yes" if teardown.ok else "no"
        final_failure = classify_failure(
            ready=ready,
            smoke_ok=smoke.ok,
            setup_ok=setup.ok,
            teardown_ok=teardown.ok,
            last_error=ready_error or smoke.last_error or teardown.last_error,
            bundle_ok=bundle_ok,
            markers=markers,
        )
        return self._report(
            ok=ok,
            stage="session",
            readiness="ready" if ready else "timeout",
            call_established=call_established,
            transport_selected=transport_selected,
            data_flow_ok=data_flow_ok,
            teardown_clean=teardown_clean,
            failure_class=final_failure.failure_class,
            failure_code=final_failure.failure_code,
            run_id=recorder.run_id,
            last_success_stage=recorder.last_success_stage,
            failed_stage=recorder.failed_stage,
            artifact_bundle=str(bundle_dir),
            process_status=[item.to_dict() for item in stopped],
            harness_steps=setup.steps + smoke.steps + teardown.steps,
            log_tails=log_tails,
            last_error="" if ok else (ready_error or smoke.last_error or teardown.last_error),
        )

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
        exact_markers = ("call_established", "teardown_done")
        prefix_markers = ("transport_selected=", "proxy_listening=", "tunnel_up=")
        for item in statuses:
            try:
                text = Path(item.log_path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped in exact_markers:
                    out.append(stripped)
                    continue
                for marker in prefix_markers:
                    if stripped.startswith(marker):
                        out.append(stripped)
                        break
            for marker in exact_markers:
                if marker in text:
                    out.append(marker)
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
        self._artifact_root.mkdir(parents=True, exist_ok=True)
        bundle_dir = self._artifact_root / f"{self._harness.topology.prefix}-{int(time.time() * 1000)}"
        bundle_dir.mkdir(parents=True, exist_ok=True)
        return bundle_dir

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
    ) -> bool:
        try:
            log_tails = _collect_log_tails(stopped)
            scenario_payload = redact_value(scenario or {})
            summary = {
                "run_id": recorder.run_id,
                "failure_class": failure_class,
                "failure_code": failure_code,
                "last_success_stage": recorder.last_success_stage,
                "failed_stage": recorder.failed_stage,
                "transport_selected": _marker_value(markers, "transport_selected="),
                "call_established": "yes" if _has_marker(markers, "call_established") else "no",
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
                "failure_class": failure_class,
                "failure_code": failure_code,
                "run_id": recorder.run_id,
                "last_success_stage": recorder.last_success_stage,
                "failed_stage": recorder.failed_stage,
                "log_tails": log_tails,
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
            recorder.write_jsonl(bundle_dir / "events.jsonl")
            for item in started:
                src = Path(item.log_path)
                if src.exists():
                    name = "server.log" if item.name == "server" else "client.log" if item.name == "client" else f"{item.name}.log"
                    (bundle_dir / name).write_text(src.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
            return True
        except OSError:
            return False
