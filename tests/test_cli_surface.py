from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_top_level_help_prioritizes_core_commands() -> None:
    from baleobala.cli import build_parser

    help_text = build_parser().format_help()

    assert "Main product path: doctor -> auth -> pair -> connect" in help_text
    assert "Core user commands: doctor, auth, pair, relay, vpn, gui" in help_text
    assert "Deprecated compatibility commands: bale-auth" in help_text
    assert "bale-auth           deprecated compatibility" in help_text
    assert "bale-proxy          debug:" in help_text
    assert "tunnel-loopback     debug:" in help_text

def test_auth_subcommand_help_text() -> None:
    from baleobala.cli import build_parser

    parser = build_parser()
    auth_parser = None
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict) and "auth" in choices:
            auth_parser = choices["auth"]
            break
    assert auth_parser is not None
    help_text = auth_parser.format_help()

    assert "login               import an existing Bale JWT into local auth state" in help_text
    assert "bale-login          recommended: real Bale phone/SMS login" in help_text


def test_tunnel_help_lists_mtproto_rpc_transport() -> None:
    import argparse

    from baleobala.vpn.cli import add_tunnel_subparser

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    add_tunnel_subparser(sub)
    tunnel_parser = next(
        action.choices["tunnel"]
        for action in parser._actions
        if isinstance(getattr(action, "choices", None), dict) and "tunnel" in action.choices
    )
    up_parser = next(
        action.choices["up"]
        for action in tunnel_parser._actions
        if isinstance(getattr(action, "choices", None), dict) and "up" in action.choices
    )
    help_text = up_parser.format_help()
    assert "mtproto_rpc" in help_text


def test_vpn_status_summary_guides_next_step(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.cli import cmd_vpn

    class Args:
        vpn_cmd = "status"

    assert cmd_vpn(Args()) == 0
    captured = capsys.readouterr()
    assert "baleobala secure connection status" in captured.out
    assert "Next step:" in captured.out


def test_vpn_status_prints_path_health_when_backend_exposes_it(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.cli import cmd_vpn

    class FakeBackend:
        def status(self):
            return {
                "backend": "linux-tun",
                "state": "running",
                "route_ready": "yes",
                "dns_ready": "yes",
                "carrier_bypass_ready": "no",
                "egress_ready": "yes",
            }

    monkeypatch.setattr("baleobala.control.backend.backend_for_profile", lambda profile: FakeBackend())

    class Args:
        vpn_cmd = "status"

    assert cmd_vpn(Args()) == 0
    captured = capsys.readouterr()
    assert "Details:" in captured.out
    assert "path_health:" in captured.out
    assert "route: yes" in captured.out
    assert "dns: yes" in captured.out
    assert "carrier_bypass: no" in captured.out
    assert "egress: yes" in captured.out


def test_bale_auth_warns_and_still_prints_jwt(monkeypatch, capsys) -> None:
    from baleobala.cli import cmd_bale_auth

    monkeypatch.setattr("baleobala.cli._run_bale_auth_login", lambda args: "jwt-token")

    class Args:
        phone = "+98912"

    assert cmd_bale_auth(Args()) == 0
    captured = capsys.readouterr()
    assert "jwt-token" in captured.out
    assert "`baleobala bale-auth` is deprecated" in captured.err
    assert "auth bale-login" in captured.err
    assert "auth login --jwt-file" in captured.err


def test_docs_align_on_production_auth_story() -> None:
    docs = {
        "README": (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
        "SETUP": (REPO_ROOT / "docs" / "SETUP.md").read_text(encoding="utf-8"),
        "INSTALL": (REPO_ROOT / "docs" / "INSTALL.md").read_text(encoding="utf-8"),
        "RELEASE": (REPO_ROOT / "docs" / "RELEASE.md").read_text(encoding="utf-8"),
        "VPN": (REPO_ROOT / "docs" / "VPN.md").read_text(encoding="utf-8"),
    }

    for name, text in docs.items():
        assert "auth bale-login" in text, name

    assert "baleobala auth login` first" not in docs["SETUP"]
    assert "Start with `baleobala auth login`" not in docs["INSTALL"]
    assert "bale-auth` still exists for backwards compatibility" in docs["README"]


def test_docs_share_main_product_path_language() -> None:
    docs = {
        "README": (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
        "SETUP": (REPO_ROOT / "docs" / "SETUP.md").read_text(encoding="utf-8"),
        "INSTALL": (REPO_ROOT / "docs" / "INSTALL.md").read_text(encoding="utf-8"),
        "RELEASE": (REPO_ROOT / "docs" / "RELEASE.md").read_text(encoding="utf-8"),
    }

    assert "doctor -> auth -> pair -> connect" in docs["README"]
    assert "doctor -> auth -> pair -> connect" in docs["SETUP"]
    assert "doctor -> auth -> pair -> connect" in docs["INSTALL"]
    assert "doctor -> auth -> pair -> connect" in docs["RELEASE"]


def test_marker_emitter_writes_exact_line(capsys) -> None:
    from baleobala.cli import _emit_marker

    _emit_marker("call_established")
    captured = capsys.readouterr()
    assert captured.err.strip() == "call_established"


def test_vpn_verdict_prints_path_health(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.cli import cmd_vpn

    class FakeBackend:
        def status(self):
            return {
                "backend": "linux-tun",
                "state": "running",
                "call_established": "yes",
                "transport_selected": "dc",
                "data_flow_ok": "yes",
                "teardown_clean": "yes",
                "route_ready": "yes",
                "dns_ready": "yes",
                "carrier_bypass_ready": "yes",
                "egress_ready": "yes",
                "failure_class": "",
                "artifact_bundle": "/tmp/bundle",
            }

    monkeypatch.setattr("baleobala.control.backend.backend_for_profile", lambda profile: FakeBackend())
    monkeypatch.setattr(
        "baleobala.control.build_product_verdict",
        lambda status, bundle_analysis=None: __import__("baleobala.control.analyzer", fromlist=["ProductVerdict"]).ProductVerdict(
            ok="yes",
            backend="linux-tun",
            state="running",
            call_established="yes",
            transport_selected="dc",
            data_flow_ok="yes",
            teardown_clean="yes",
            route_ready="yes",
            dns_ready="yes",
            carrier_bypass_ready="yes",
            egress_ready="yes",
            failure_class="",
            artifact_bundle="/tmp/bundle",
            analysis_classification="accepted_flow",
            analysis_reason="bundle indicates accepted flow",
        ),
    )

    class Args:
        vpn_cmd = "verdict"
        bundle_path = ""
        json = False

    assert cmd_vpn(Args()) == 0
    captured = capsys.readouterr()
    assert "vpn verdict summary" in captured.out
    assert "analysis_reason: bundle indicates accepted flow" in captured.out
    assert "path_health:" in captured.out
    assert "route: yes" in captured.out
    assert "dns: yes" in captured.out
    assert "carrier_bypass: yes" in captured.out
    assert "egress: yes" in captured.out


def test_vpn_smoke_prints_path_health(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.cli import cmd_vpn

    class FakeBackend:
        def status(self):
            return {
                "backend": "linux-tun",
                "state": "running",
                "control_ready": "yes",
                "data_path_ready": "yes",
                "route_ready": "yes",
                "dns_ready": "no",
                "carrier_bypass_ready": "yes",
                "egress_ready": "no",
                "last_error": "",
            }

    class FakeSmokeReport:
        ok = False

        def to_dict(self):
            return {
                "ok": "no",
                "backend": "linux-tun",
                "state": "running",
                "endpoint": "",
                "control_ready": "yes",
                "data_path_ready": "yes",
                "route_ready": "yes",
                "dns_ready": "no",
                "carrier_bypass_ready": "yes",
                "egress_ready": "no",
                "probe_ok": "yes",
                "probe_kind": "none",
                "probe_detail": "connected",
                "last_error": "",
            }

    monkeypatch.setattr("baleobala.control.backend.backend_for_profile", lambda profile: FakeBackend())
    monkeypatch.setattr("baleobala.control.smoke_backend_status", lambda status, timeout=1.0: FakeSmokeReport())

    class Args:
        vpn_cmd = "smoke"
        timeout = 1.0
        json = False

    assert cmd_vpn(Args()) == 2
    captured = capsys.readouterr()
    assert "vpn smoke summary" in captured.out
    assert "path_health:" in captured.out
    assert "route: yes" in captured.out
    assert "dns: no" in captured.out
    assert "carrier_bypass: yes" in captured.out
    assert "egress: no" in captured.out
    assert "path_hints:" in captured.out
    assert "DNS mismatch in bundle snapshot" in captured.out
    assert "TCP egress probe did not pass" in captured.out


def test_vpn_analyze_bundle_prints_path_health(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.cli import cmd_vpn

    monkeypatch.setattr(
        "baleobala.control.analyze_bundle",
        lambda bundle_path: __import__("baleobala.control.analyzer", fromlist=["BundleAnalysis"]).BundleAnalysis(
            classification="accepted_flow",
            reason="bundle indicates accepted flow",
            bundle_path=str(bundle_path),
            ok="yes",
            failure_code="",
            last_success_stage="teardown",
            failed_stage="",
            transport_selected="dc",
            retry_count="0",
        ),
    )
    monkeypatch.setattr(
        "baleobala.control.bundle_status",
        lambda bundle_path: {
            "artifact_bundle": str(bundle_path),
            "route_ready": "yes",
            "dns_ready": "yes",
            "carrier_bypass_ready": "yes",
            "egress_ready": "yes",
        },
    )

    class Args:
        vpn_cmd = "analyze-bundle"
        bundle_path = "/tmp/fake-bundle"
        json = False

    assert cmd_vpn(Args()) == 0
    captured = capsys.readouterr()
    assert "vpn analyze-bundle result" in captured.out
    assert "transport_selected: dc" in captured.out
    assert "path_health:" in captured.out
    assert "route: yes" in captured.out
    assert "dns: yes" in captured.out
    assert "carrier_bypass: yes" in captured.out
    assert "egress: yes" in captured.out


def test_vpn_analyze_bundle_json_includes_path_health(monkeypatch, capsys, tmp_path) -> None:
    import json

    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.cli import cmd_vpn

    monkeypatch.setattr(
        "baleobala.control.analyze_bundle",
        lambda bundle_path: __import__("baleobala.control.analyzer", fromlist=["BundleAnalysis"]).BundleAnalysis(
            classification="infra_flake",
            reason="dns_config_failed",
            bundle_path=str(bundle_path),
            ok="no",
            failure_code="dns_config_failed",
            last_success_stage="smoke",
            failed_stage="smoke",
            transport_selected="dc",
            retry_count="1",
        ),
    )
    monkeypatch.setattr(
        "baleobala.control.bundle_status",
        lambda bundle_path: {
            "artifact_bundle": str(bundle_path),
            "route_ready": "yes",
            "dns_ready": "no",
            "carrier_bypass_ready": "no",
            "egress_ready": "yes",
        },
    )

    class Args:
        vpn_cmd = "analyze-bundle"
        bundle_path = "/tmp/fake-bundle"
        json = True

    assert cmd_vpn(Args()) == 2
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["classification"] == "infra_flake"
    assert payload["route_ready"] == "yes"
    assert payload["dns_ready"] == "no"
    assert payload["carrier_bypass_ready"] == "no"
    assert payload["egress_ready"] == "yes"


def test_vpn_analyze_bundle_prints_failure_hints(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.cli import cmd_vpn

    monkeypatch.setattr(
        "baleobala.control.analyze_bundle",
        lambda bundle_path: __import__("baleobala.control.analyzer", fromlist=["BundleAnalysis"]).BundleAnalysis(
            classification="product_bug",
            reason="route_program_failed",
            bundle_path=str(bundle_path),
            ok="no",
            failure_code="route_program_failed",
            last_success_stage="runtime_setup",
            failed_stage="smoke",
            transport_selected="dc",
            retry_count="0",
        ),
    )
    monkeypatch.setattr(
        "baleobala.control.bundle_status",
        lambda bundle_path: {
            "artifact_bundle": str(bundle_path),
            "route_ready": "no",
            "dns_ready": "yes",
            "carrier_bypass_ready": "no",
            "egress_ready": "yes",
        },
    )

    class Args:
        vpn_cmd = "analyze-bundle"
        bundle_path = "/tmp/fake-bundle"
        json = False

    assert cmd_vpn(Args()) == 2
    captured = capsys.readouterr()
    assert "path_hints:" in captured.out
    assert "tunnel route programming looks incomplete" in captured.out
    assert "carrier bypass route is missing" in captured.out
