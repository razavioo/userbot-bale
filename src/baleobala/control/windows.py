"""Windows system-proxy helpers for the development VPN path."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from baleobala.control.paths import config_dir
from baleobala.control.store import JsonStore


Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class WinHttpProxyState:
    mode: str
    server: str = ""
    bypass: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "mode": self.mode,
            "server": self.server,
            "bypass": self.bypass,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "WinHttpProxyState":
        return cls(
            mode=str(data.get("mode", "direct")),
            server=str(data.get("server", "")),
            bypass=str(data.get("bypass", "")),
        )


class WindowsSystemProxySession:
    """Apply and restore WinHTTP proxy settings for local development."""

    def __init__(
        self,
        *,
        listen_host: str = "127.0.0.1",
        listen_port: int = 1080,
        state_path: Path | None = None,
        runner: Runner | None = None,
    ) -> None:
        self.listen_host = listen_host
        self.listen_port = listen_port
        self._runner = runner or subprocess.run
        self._state_store = JsonStore(state_path or (config_dir() / "windows_system_proxy.json"))
        self._snapshot: WinHttpProxyState | None = None
        self._active = False

    def start(self) -> None:
        if self._active:
            return
        self._snapshot = self._read_proxy_state()
        self._state_store.save(self._snapshot.to_dict())
        self._apply_proxy()
        self._active = True

    def stop(self) -> None:
        if not self._active and self._snapshot is None:
            self.restore_saved_state(state_path=self._state_store.path, runner=self._runner)
            self._active = False
            return
        snapshot = self._snapshot or self._load_saved_state()
        if snapshot is not None:
            self._restore_proxy(snapshot)
        self._snapshot = None
        self._active = False
        try:
            self._state_store.path.unlink()
        except FileNotFoundError:
            pass

    @property
    def active(self) -> bool:
        return self._active

    def status(self) -> dict[str, str]:
        return {
            "active": "yes" if self._active else "no",
            "proxy": f"{self.listen_host}:{self.listen_port}",
            "mode": "winhttp-system-proxy",
            "state": "running" if self._active else "stopped",
        }

    @classmethod
    def restore_saved_state(
        cls,
        *,
        state_path: Path | None = None,
        runner: Runner | None = None,
    ) -> bool:
        store = JsonStore(state_path or (config_dir() / "windows_system_proxy.json"))
        payload = store.load(default=None)
        if not isinstance(payload, dict):
            return False
        session = cls(state_path=store.path, runner=runner)
        session._restore_proxy(WinHttpProxyState.from_dict(payload))
        try:
            store.path.unlink()
        except FileNotFoundError:
            pass
        return True

    def _load_saved_state(self) -> WinHttpProxyState | None:
        payload = self._state_store.load(default=None)
        if not isinstance(payload, dict):
            return None
        return WinHttpProxyState.from_dict(payload)

    def _apply_proxy(self) -> None:
        proxy = f"{self.listen_host}:{self.listen_port}"
        self._run(["netsh", "winhttp", "set", "proxy", proxy, "bypass-list=localhost;127.0.0.1;::1"])

    def _restore_proxy(self, snapshot: WinHttpProxyState) -> None:
        if snapshot.mode == "direct":
            self._run(["netsh", "winhttp", "reset", "proxy"])
            return
        if snapshot.mode == "auto":
            self._run(["netsh", "winhttp", "reset", "proxy"])
            return
        cmd = ["netsh", "winhttp", "set", "proxy", snapshot.server]
        if snapshot.bypass:
            cmd.append(f"bypass-list={snapshot.bypass}")
        self._run(cmd)

    def _read_proxy_state(self) -> WinHttpProxyState:
        result = self._run(["netsh", "winhttp", "show", "proxy"])
        return _parse_winhttp_proxy_state(result.stdout or "")

    def _run(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return self._runner(cmd, check=True, capture_output=True, text=True)


def _parse_winhttp_proxy_state(text: str) -> WinHttpProxyState:
    lowered = text.lower()
    if "direct access" in lowered:
        return WinHttpProxyState(mode="direct")
    if "automatically detect settings" in lowered or "automatic proxy" in lowered:
        return WinHttpProxyState(mode="auto")

    server = ""
    bypass = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        lower_line = line.lower()
        if lower_line.startswith("proxy server"):
            _, _, server = line.partition(":")
            server = server.strip()
        elif lower_line.startswith("bypass list"):
            _, _, bypass = line.partition(":")
            bypass = bypass.strip()
    if server:
        return WinHttpProxyState(mode="proxy", server=server, bypass=bypass)
    return WinHttpProxyState(mode="direct")
