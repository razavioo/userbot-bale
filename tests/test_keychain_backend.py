from __future__ import annotations

import subprocess

from baleobala.control.keychain import KeychainSecretBackend


def test_keychain_security_env_drops_python_launcher_vars(monkeypatch) -> None:
    monkeypatch.setenv("PYTHONHOME", "/tmp/pythonhome")
    monkeypatch.setenv("PYTHONPATH", "/tmp/pythonpath")
    monkeypatch.setenv("SSL_CERT_FILE", "/tmp/ca.pem")

    env = KeychainSecretBackend()._security_env()

    assert "PYTHONHOME" not in env
    assert "PYTHONPATH" not in env
    assert env["SSL_CERT_FILE"] == "/tmp/ca.pem"


def test_keychain_load_uses_sanitized_env(monkeypatch) -> None:
    calls: list[dict[str, str]] = []

    def fake_run(cmd, *, check, capture_output, text, env):  # noqa: ANN001
        calls.append(env)
        return subprocess.CompletedProcess(cmd, 0, stdout="secret\n", stderr="")

    monkeypatch.setenv("PYTHONPATH", "/tmp/pythonpath")
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert KeychainSecretBackend().load("auth.jwt") == "secret"
    assert calls
    assert "PYTHONPATH" not in calls[0]
