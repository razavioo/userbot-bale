from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_top_level_help_prioritizes_core_commands() -> None:
    from userbot_bale.cli import build_parser

    help_text = build_parser().format_help()

    assert "Main product path: doctor -> auth -> pair -> connect" in help_text
    assert "Core user commands: doctor, auth, pair, relay, vpn, gui" in help_text
    assert "Deprecated compatibility commands: bale-auth" in help_text
    assert "bale-auth           deprecated compatibility" in help_text
    assert "bale-proxy          debug:" in help_text
    assert "tunnel-loopback     debug:" in help_text


def test_userbot_and_mcp_commands_are_exposed() -> None:
    from userbot_bale.cli import build_parser

    parser = build_parser()
    userbot_args = parser.parse_args(["userbot", "allow-peer", "123"])
    mcp_args = parser.parse_args(["mcp", "serve"])
    send_args = parser.parse_args(["userbot", "send", "123", "hello"])
    dialogs_args = parser.parse_args(["userbot", "dialogs", "--limit", "10"])
    messages_args = parser.parse_args(["userbot", "messages", "123", "--limit", "15"])
    status_args = parser.parse_args(["userbot", "status"])
    read_args = parser.parse_args(["userbot", "mark-read", "123", "999999"])
    search_args = parser.parse_args(["userbot", "search", "keyword", "--peer-id", "123"])
    search_remote_args = parser.parse_args(
        ["userbot", "search-remote", "invoice", "--peer-id", "123", "--limit", "5"],
    )
    shared_media_args = parser.parse_args(
        ["userbot", "shared-media", "123", "--limit", "10", "--content-type", "2"],
    )
    rpc_paths_args = parser.parse_args(
        ["userbot", "rpc-paths", "--service", "messaging", "--query", "LoadHistory", "--limit", "20"],
    )
    whoami_args = parser.parse_args(["userbot", "whoami"])
    contacts_args = parser.parse_args(["userbot", "search-contacts", "john"])
    resolve_args = parser.parse_args(["userbot", "resolve-phone", "+989123456789"])

    assert userbot_args.userbot_cmd == "allow-peer"
    assert userbot_args.peer_id == 123
    assert mcp_args.mcp_cmd == "serve"
    assert send_args.userbot_cmd == "send"
    assert send_args.peer_id == 123
    assert send_args.text == "hello"
    assert dialogs_args.limit == 10
    assert messages_args.peer_id == 123
    assert messages_args.limit == 15
    assert status_args.userbot_cmd == "status"
    assert read_args.userbot_cmd == "mark-read"
    assert read_args.date == 999999
    assert search_args.userbot_cmd == "search"
    assert search_args.query == "keyword"
    assert search_remote_args.userbot_cmd == "search-remote"
    assert search_remote_args.query == "invoice"
    assert search_remote_args.peer_id == 123
    assert search_remote_args.limit == 5
    assert shared_media_args.userbot_cmd == "shared-media"
    assert shared_media_args.peer_id == 123
    assert shared_media_args.limit == 10
    assert shared_media_args.content_type == 2
    assert rpc_paths_args.userbot_cmd == "rpc-paths"
    assert rpc_paths_args.service == "messaging"
    assert rpc_paths_args.query == "LoadHistory"
    assert rpc_paths_args.limit == 20
    assert whoami_args.userbot_cmd == "whoami"
    assert contacts_args.userbot_cmd == "search-contacts"
    assert contacts_args.query == "john"
    assert resolve_args.userbot_cmd == "resolve-phone"
    assert resolve_args.phone == "+989123456789"

def test_auth_subcommand_help_text() -> None:
    from userbot_bale.cli import build_parser

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

    from userbot_bale.vpn.cli import add_tunnel_subparser

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


def _exit_node_mesh_parser():
    """Build the `tunnel exit-node-mesh` subparser in isolation."""
    import argparse

    from userbot_bale.vpn.cli import add_tunnel_subparser

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    add_tunnel_subparser(sub)
    tunnel_parser = next(
        action.choices["tunnel"]
        for action in parser._actions
        if isinstance(getattr(action, "choices", None), dict) and "tunnel" in action.choices
    )
    return next(
        action.choices["exit-node-mesh"]
        for action in tunnel_parser._actions
        if isinstance(getattr(action, "choices", None), dict)
        and "exit-node-mesh" in action.choices
    )


def test_exit_node_mesh_no_coordinator_surface() -> None:
    """The mesh parser must not expose coordinator-mode flags after the
    coordinator removal — clients dial relays directly."""
    mesh = _exit_node_mesh_parser()
    help_text = mesh.format_help()
    for removed in ("--coordinator-peer-id", "--relay-peer-id", "--standalone",
                    "--heartbeat-interval", "--relay-id-prefix"):
        assert removed not in help_text, f"{removed!r} should have been removed"


def test_exit_node_mesh_minimal_args_accepted() -> None:
    """The only required positional input is a Bale JWT — the rest
    has reasonable defaults."""
    mesh = _exit_node_mesh_parser()
    args = mesh.parse_args(["--bale-jwt-file", "/tmp/jwt"])
    assert args.bale_jwt_file == ["/tmp/jwt"]
    assert getattr(args, "pool_cidr", None) == "10.77.0.0/16"
    assert getattr(args, "max_peers_per_server_jwt", None) == 4


def test_vpn_status_summary_guides_next_step(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.setenv("USERBOT_BALE_HOME", str(tmp_path))
    from userbot_bale.cli import cmd_vpn

    class Args:
        vpn_cmd = "status"

    assert cmd_vpn(Args()) == 0
    captured = capsys.readouterr()
    assert "userbot-bale secure connection status" in captured.out
    assert "Next step:" in captured.out


def test_vpn_status_prints_path_health_when_backend_exposes_it(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.setenv("USERBOT_BALE_HOME", str(tmp_path))
    from userbot_bale.cli import cmd_vpn

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

    monkeypatch.setattr("userbot_bale.control.backend.backend_for_profile", lambda profile: FakeBackend())

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
    from userbot_bale.cli import cmd_bale_auth

    monkeypatch.setattr("userbot_bale.cli._run_bale_auth_login", lambda args: "jwt-token")

    class Args:
        phone = "+98912"

    assert cmd_bale_auth(Args()) == 0
    captured = capsys.readouterr()
    assert "jwt-token" in captured.out
    assert "`userbot-bale bale-auth` is deprecated" in captured.err
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

    assert "userbot-bale auth login` first" not in docs["SETUP"]
    assert "Start with `userbot-bale auth login`" not in docs["INSTALL"]
    # README must document the bale-login command (canonical auth entry point).
    assert "bale-login" in docs["README"]


def test_docs_share_main_product_path_language() -> None:
    docs = {
        "README": (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
        "SETUP": (REPO_ROOT / "docs" / "SETUP.md").read_text(encoding="utf-8"),
        "INSTALL": (REPO_ROOT / "docs" / "INSTALL.md").read_text(encoding="utf-8"),
        "RELEASE": (REPO_ROOT / "docs" / "RELEASE.md").read_text(encoding="utf-8"),
    }

    # Each doc must mention the auth step (bale-login) as part of the setup flow.
    for name, text in docs.items():
        assert "bale-login" in text or "auth" in text.lower(), (
            f"{name} does not mention the auth/bale-login step"
        )


def test_marker_emitter_writes_exact_line(capsys) -> None:
    from userbot_bale.cli import _emit_marker

    _emit_marker("call_established")
    captured = capsys.readouterr()
    assert captured.err.strip() == "call_established"


def test_vpn_verdict_prints_path_health(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.setenv("USERBOT_BALE_HOME", str(tmp_path))
    from userbot_bale.cli import cmd_vpn

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

    monkeypatch.setattr("userbot_bale.control.backend.backend_for_profile", lambda profile: FakeBackend())
    monkeypatch.setattr(
        "userbot_bale.control.build_product_verdict",
        lambda status, bundle_analysis=None: __import__("userbot_bale.control.analyzer", fromlist=["ProductVerdict"]).ProductVerdict(
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
    monkeypatch.setenv("USERBOT_BALE_HOME", str(tmp_path))
    from userbot_bale.cli import cmd_vpn

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

    monkeypatch.setattr("userbot_bale.control.backend.backend_for_profile", lambda profile: FakeBackend())
    monkeypatch.setattr("userbot_bale.control.smoke_backend_status", lambda status, timeout=1.0: FakeSmokeReport())

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
    monkeypatch.setenv("USERBOT_BALE_HOME", str(tmp_path))
    from userbot_bale.cli import cmd_vpn

    monkeypatch.setattr(
        "userbot_bale.control.analyze_bundle",
        lambda bundle_path: __import__("userbot_bale.control.analyzer", fromlist=["BundleAnalysis"]).BundleAnalysis(
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
        "userbot_bale.control.bundle_status",
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

    monkeypatch.setenv("USERBOT_BALE_HOME", str(tmp_path))
    from userbot_bale.cli import cmd_vpn

    monkeypatch.setattr(
        "userbot_bale.control.analyze_bundle",
        lambda bundle_path: __import__("userbot_bale.control.analyzer", fromlist=["BundleAnalysis"]).BundleAnalysis(
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
        "userbot_bale.control.bundle_status",
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
    monkeypatch.setenv("USERBOT_BALE_HOME", str(tmp_path))
    from userbot_bale.cli import cmd_vpn

    monkeypatch.setattr(
        "userbot_bale.control.analyze_bundle",
        lambda bundle_path: __import__("userbot_bale.control.analyzer", fromlist=["BundleAnalysis"]).BundleAnalysis(
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
        "userbot_bale.control.bundle_status",
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
