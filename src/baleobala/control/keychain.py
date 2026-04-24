"""Secret storage helpers for macOS Keychain and portable fallbacks."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from baleobala.control.paths import config_dir


@dataclass(frozen=True)
class SecretRecord:
    name: str
    value: str


class SecretBackend:
    def load(self, name: str) -> str | None:
        raise NotImplementedError

    def save(self, name: str, value: str) -> None:
        raise NotImplementedError

    def delete(self, name: str) -> None:
        raise NotImplementedError


class FileSecretBackend(SecretBackend):
    """Portable secret storage used outside macOS and in tests."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or (config_dir() / "secrets")

    def _path(self, name: str) -> Path:
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
        return self.directory / f"{digest}.secret"

    def load(self, name: str) -> str | None:
        path = self._path(name)
        if not path.exists():
            return None
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return None

    def save(self, name: str, value: str) -> None:
        path = self._path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def delete(self, name: str) -> None:
        try:
            self._path(name).unlink()
        except FileNotFoundError:
            pass


class KeychainSecretBackend(SecretBackend):
    """macOS Keychain-backed generic password storage."""

    def __init__(self, service: str = "com.baleobala.auth", account: str = "baleobala") -> None:
        self.service = service
        self.account = account

    def _security_env(self) -> dict[str, str]:
        env = os.environ.copy()
        for key in ("PYTHONHOME", "PYTHONPATH"):
            env.pop(key, None)
        return env

    def load(self, name: str) -> str | None:
        account = self._account(name)
        cmd = [
            "security",
            "find-generic-password",
            "-a",
            account,
            "-s",
            self.service,
            "-w",
        ]
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True, env=self._security_env())
        except (FileNotFoundError, subprocess.CalledProcessError):
            return None
        value = result.stdout.strip()
        return value or None

    def save(self, name: str, value: str) -> None:
        account = self._account(name)
        cmd = [
            "security",
            "add-generic-password",
            "-U",
            "-a",
            account,
            "-s",
            self.service,
            "-w",
            value,
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True, env=self._security_env())

    def delete(self, name: str) -> None:
        account = self._account(name)
        cmd = [
            "security",
            "delete-generic-password",
            "-a",
            account,
            "-s",
            self.service,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, env=self._security_env())
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass

    def _account(self, name: str) -> str:
        return f"{self.account}:{name}"


def default_secret_backend() -> SecretBackend:
    if os.environ.get("BALEOBALA_SECRET_BACKEND") == "file":
        return FileSecretBackend()
    if os.environ.get("BALEOBALA_SECRET_BACKEND") == "keychain":
        return KeychainSecretBackend()
    if sys.platform == "darwin":
        return KeychainSecretBackend()
    return FileSecretBackend()
