"""macOS LaunchAgent helpers for the VPN control plane."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from baleobala.control.paths import config_dir
from baleobala.control.vpn import VpnStore


Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class LaunchAgentSpec:
    label: str
    program_arguments: list[str]
    run_at_load: bool = True
    keep_alive: bool = True
    working_directory: str | None = None


class MacOSLaunchAgentManager:
    """Install and control a per-user LaunchAgent for `baleobala vpn up`."""

    def __init__(
        self,
        *,
        label: str = "com.baleobala.vpn",
        plist_path: Path | None = None,
        runner: Runner | None = None,
    ) -> None:
        self.label = label
        self._plist_path = plist_path or (Path.home() / "Library" / "LaunchAgents" / f"{label}.plist")
        self._runner = runner or subprocess.run

    def spec(
        self,
        profile_id: str | None = None,
        *,
        mode: str = "vpn",
        jwt_file: str | None = None,
        proxy_secret_file: str | None = None,
    ) -> LaunchAgentSpec:
        profile = VpnStore().load()
        if mode == "vpn":
            args = [
                sys.executable,
                "-m",
                "baleobala.cli",
                "vpn",
                "up",
            ]
            if profile_id:
                args.extend(["--profile-id", profile_id])
            elif profile is not None and profile.profile_id:
                args.extend(["--profile-id", profile.profile_id])
        elif mode == "proxy-client":
            if profile is None or profile.peer_id is None:
                raise ValueError("proxy-client LaunchAgent requires a saved profile with peer_id")
            args = [
                sys.executable,
                "-m",
                "baleobala.cli",
                "bale-proxy",
                "client",
                "--transport",
                "dc",
                "--bale-jwt-file",
                str(Path(jwt_file or "~/.bale_jwt_b").expanduser()),
                "--peer-id",
                str(profile.peer_id),
                "--listen-host",
                profile.listen_host,
                "--listen-port",
                str(profile.listen_port),
            ]
            if proxy_secret_file:
                args.extend(["--proxy-secret-file", str(Path(proxy_secret_file).expanduser())])
            elif profile.proxy_secret:
                args.extend(["--proxy-secret", profile.proxy_secret])
        else:
            raise ValueError(f"unknown LaunchAgent mode: {mode}")
        return LaunchAgentSpec(
            label=self.label,
            program_arguments=args,
            working_directory=str(Path.cwd()),
        )

    def install(
        self,
        profile_id: str | None = None,
        *,
        mode: str = "vpn",
        jwt_file: str | None = None,
        proxy_secret_file: str | None = None,
        ssl_cert_file: str | None = None,
    ) -> Path:
        self._plist_path.parent.mkdir(parents=True, exist_ok=True)
        spec = self.spec(
            profile_id=profile_id,
            mode=mode,
            jwt_file=jwt_file,
            proxy_secret_file=proxy_secret_file,
        )
        environment = {
            "BALEOBALA_HOME": str(config_dir()),
            "HOME": str(Path.home()),
            "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONUNBUFFERED": "1",
            "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        }
        if ssl_cert_file:
            environment["SSL_CERT_FILE"] = str(Path(ssl_cert_file).expanduser())
        payload = {
            "Label": spec.label,
            "ProgramArguments": spec.program_arguments,
            "RunAtLoad": spec.run_at_load,
            "KeepAlive": spec.keep_alive,
            "WorkingDirectory": spec.working_directory or str(Path.cwd()),
            "EnvironmentVariables": environment,
            "StandardOutPath": str(config_dir() / f"{self.label}.out.log"),
            "StandardErrorPath": str(config_dir() / f"{self.label}.err.log"),
        }
        with self._plist_path.open("wb") as fh:
            plistlib.dump(payload, fh)
        self._run([
            "launchctl",
            "bootstrap",
            f"gui/{os.getuid()}",
            str(self._plist_path),
        ], check=False)
        return self._plist_path

    def remove(self) -> None:
        self.stop()
        try:
            self._plist_path.unlink()
        except FileNotFoundError:
            pass

    def start(self) -> None:
        self._run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{self.label}"], check=False)

    def stop(self) -> None:
        self._run(["launchctl", "bootout", f"gui/{os.getuid()}", str(self._plist_path)], check=False)

    def status(self) -> dict[str, str]:
        profile = VpnStore().load()
        return {
            "label": self.label,
            "plist": str(self._plist_path),
            "installed": "yes" if self._plist_path.exists() else "no",
            "profile_id": profile.profile_id if profile is not None else "none",
            "backend": profile.backend if profile is not None else "none",
        }

    def _run(self, cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return self._runner(cmd, check=check, capture_output=True, text=True)
