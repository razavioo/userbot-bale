from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_top_level_help_prioritizes_core_commands() -> None:
    from baleobala.cli import build_parser

    help_text = build_parser().format_help()

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
