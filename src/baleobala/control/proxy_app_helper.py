"""JSON control bridge for the macOS BaleobalaProxy app.

Wraps the `bale-proxy system` workflow and the Bale phone-number sign-in flow
behind a small JSON-line protocol the SwiftUI app talks to over stdin/stdout.
The Swift side never shells out to networksetup directly — it asks this helper.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_LISTEN_HOST = "127.0.0.1"
DEFAULT_LISTEN_PORT = 1080
DEFAULT_NETWORK_SERVICE = "Wi-Fi"
DEFAULT_RELAY_PEER_ID = "1519372475"

_SETTINGS_DIR = Path.home() / ".baleobala"
_SETTINGS_PATH = _SETTINGS_DIR / "proxy_app_settings.json"
_RUNTIME_DIR = _SETTINGS_DIR / "proxy_app_runtime"
_PID_FILE = _RUNTIME_DIR / "proxy.pid"
_META_FILE = _RUNTIME_DIR / "proxy.json"
_LOG_FILE = Path.home() / "Library" / "Logs" / "baleobala" / "proxy-app.log"

_AUTH_SESSIONS: dict[str, Any] = {}


@dataclass
class ProxySettings:
    listen_host: str = DEFAULT_LISTEN_HOST
    listen_port: int = DEFAULT_LISTEN_PORT
    network_service: str = DEFAULT_NETWORK_SERVICE
    relay_peer_id: str = DEFAULT_RELAY_PEER_ID
    transport: str = "dc"

    @classmethod
    def load(cls) -> "ProxySettings":
        if not _SETTINGS_PATH.exists():
            return cls()
        try:
            data = json.loads(_SETTINGS_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return cls()
        return cls(
            listen_host=str(data.get("listen_host") or DEFAULT_LISTEN_HOST),
            listen_port=int(data.get("listen_port") or DEFAULT_LISTEN_PORT),
            network_service=str(data.get("network_service") or DEFAULT_NETWORK_SERVICE),
            relay_peer_id=str(data.get("relay_peer_id") or DEFAULT_RELAY_PEER_ID),
            transport=str(data.get("transport") or "dc"),
        )

    def save(self) -> None:
        _SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        _SETTINGS_PATH.write_text(json.dumps(asdict(self), indent=2))


@dataclass
class Result:
    ok: bool
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "message": self.message, "data": self.data}


# ---------------------------------------------------------------------------
# Process / system-proxy helpers
# ---------------------------------------------------------------------------

def _networksetup(*args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["/usr/sbin/networksetup", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError:
        return 127, "networksetup not found"
    except subprocess.TimeoutExpired:
        return 124, "networksetup timed out"


def _disable_system_proxies(service: str) -> None:
    for kind in ("setwebproxystate", "setsecurewebproxystate", "setsocksfirewallproxystate"):
        _networksetup(kind, service, "off")


def _list_network_services() -> list[str]:
    code, out = _networksetup("-listallnetworkservices")
    if code != 0:
        return []
    services: list[str] = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("*") or "An asterisk" in line:
            continue
        services.append(line)
    return services


def _disable_all_proxies_everywhere() -> None:
    """Belt-and-suspenders: clear web/secureweb/SOCKS proxy on every
    discoverable network service. Used by stopProxy so a stale or
    misconfigured run can't leave the user with broken networking even
    if `settings.network_service` no longer matches the active one."""
    for svc in _list_network_services():
        _disable_system_proxies(svc)


def _socks_proxy_state(service: str) -> dict[str, str]:
    """Parse `networksetup -getsocksfirewallproxy <service>` output into
    a {Enabled, Server, Port} dict so the UI can verify the system proxy
    really is set, not just that the subprocess is alive."""
    code, out = _networksetup("-getsocksfirewallproxy", service)
    info: dict[str, str] = {"Enabled": "No", "Server": "", "Port": ""}
    if code != 0:
        return info
    for line in out.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if key in info:
            info[key] = value
    return info


def _read_pid() -> int | None:
    if not _PID_FILE.exists():
        return None
    try:
        pid = int(_PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return None
    if pid <= 0:
        return None
    return pid


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _kill_pid(pid: int) -> None:
    """Send SIGTERM, give the proxy ~6s to teardown its LiveKit room
    and restore the system-proxy snapshot, then SIGKILL if it's still
    around. The previous 2s window was too short — bale-proxy system's
    teardown_done marker often takes 3-4s when the LiveKit thread has
    to exit cleanly."""
    deadlines = [(signal.SIGTERM, 60), (signal.SIGKILL, 20)]
    for sig, ticks in deadlines:
        try:
            os.killpg(os.getpgid(pid), sig)
        except (ProcessLookupError, PermissionError):
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                return
        for _ in range(ticks):
            if not _pid_alive(pid):
                return
            time.sleep(0.1)


def _read_meta() -> dict[str, Any]:
    if not _META_FILE.exists():
        return {}
    try:
        return json.loads(_META_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_meta(meta: dict[str, Any]) -> None:
    _RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    _META_FILE.write_text(json.dumps(meta, indent=2))


def _proxy_running() -> bool:
    pid = _read_pid()
    if pid is None:
        return False
    if not _pid_alive(pid):
        try:
            _PID_FILE.unlink()
        except OSError:
            pass
        return False
    return True


# ---------------------------------------------------------------------------
# Auth (Bale phone OTP)
# ---------------------------------------------------------------------------

def _account_path() -> Path:
    return Path.home() / ".baleobala" / "accounts" / "account-1.jwt"


def _logged_in() -> bool:
    p = _account_path()
    return p.exists() and p.stat().st_size > 0


def _auth_status() -> dict[str, Any]:
    p = _account_path()
    info: dict[str, Any] = {
        "logged_in": _logged_in(),
        "account_path": str(p),
        "phone": "",
    }
    meta_path = p.with_suffix(".meta.json")
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            info["phone"] = str(meta.get("phone") or "")
        except (json.JSONDecodeError, OSError):
            pass
    return info


def _save_jwt(jwt: str, *, phone: str | None) -> Path:
    p = _account_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(jwt)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    if phone:
        meta_path = p.with_suffix(".meta.json")
        meta_path.write_text(json.dumps({"phone": phone, "saved_at": time.time()}))
    return p


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _state_payload() -> dict[str, Any]:
    settings = ProxySettings.load()
    meta = _read_meta()
    running = _proxy_running()
    socks_state = _socks_proxy_state(settings.network_service) if running else {"Enabled": "No", "Server": "", "Port": ""}
    system_proxy_active = socks_state.get("Enabled", "No") == "Yes"
    return {
        "auth": _auth_status(),
        "proxy": {
            "running": running,
            "pid": _read_pid() if running else None,
            "listen_host": settings.listen_host,
            "listen_port": settings.listen_port,
            "service": settings.network_service,
            "relay_peer_id": settings.relay_peer_id,
            "started_at": meta.get("started_at") if running else None,
            # Distinguishes "subprocess alive" from "macOS networksetup
            # actually points Wi-Fi traffic at our SOCKS listener". The
            # UI uses this so a half-up state can't masquerade as
            # "Connected" with no proxy applied.
            "system_proxy_active": system_proxy_active,
            "system_proxy_server": socks_state.get("Server", ""),
            "system_proxy_port": socks_state.get("Port", ""),
        },
        "settings": asdict(settings),
        "log_path": str(_LOG_FILE),
    }


def _cmd_status(_payload: dict[str, Any]) -> Result:
    return Result(True, data=_state_payload())


def _cmd_save_settings(payload: dict[str, Any]) -> Result:
    settings = ProxySettings.load()
    if "listen_port" in payload:
        try:
            settings.listen_port = int(payload["listen_port"])
        except (TypeError, ValueError):
            return Result(False, "Invalid port", data=_state_payload())
    if "network_service" in payload:
        settings.network_service = str(payload["network_service"]).strip() or DEFAULT_NETWORK_SERVICE
    if "relay_peer_id" in payload:
        settings.relay_peer_id = str(payload["relay_peer_id"]).strip() or DEFAULT_RELAY_PEER_ID
    if "transport" in payload:
        settings.transport = str(payload["transport"]).strip() or "dc"
    settings.save()
    return Result(True, "Settings saved.", data=_state_payload())


def _cmd_list_services(_payload: dict[str, Any]) -> Result:
    code, out = _networksetup("-listallnetworkservices")
    services: list[str] = []
    if code == 0:
        for line in out.splitlines():
            line = line.strip()
            if not line or line.startswith("*") or "An asterisk" in line:
                continue
            services.append(line)
    return Result(True, data={"services": services})


def _cmd_auth_start(payload: dict[str, Any]) -> Result:
    phone = str(payload.get("phone", "")).strip()
    if not phone:
        return Result(False, "Enter a phone number.")
    try:
        from baleobala.bale.auth import BaleAuth
    except Exception as exc:  # noqa: BLE001
        return Result(False, f"Bale auth unavailable: {exc}")
    session_id = str(int(time.time() * 1000))
    auth = BaleAuth(session_id=session_id)
    try:
        tx = auth.start_phone_auth(int(phone.lstrip("+")))
    except Exception as exc:  # noqa: BLE001
        return Result(False, f"Could not send code: {exc}")
    _AUTH_SESSIONS[str(tx)] = (auth, phone)
    return Result(True, "SMS code sent.", data={"transaction_hash": str(tx)})


def _cmd_auth_verify(payload: dict[str, Any]) -> Result:
    tx = str(payload.get("transaction_hash", "")).strip()
    code = str(payload.get("code", "")).strip()
    if not tx or not code:
        return Result(False, "Missing transaction or code.")
    entry = _AUTH_SESSIONS.get(tx)
    if entry is None:
        return Result(False, "Auth session expired. Request a new code.")
    auth, phone = entry
    try:
        session = auth.validate_code(code, transaction_hash=tx)
    except Exception as exc:  # noqa: BLE001
        return Result(False, f"Verification failed: {exc}")
    _save_jwt(session.jwt, phone=phone)
    _AUTH_SESSIONS.pop(tx, None)
    return Result(True, "Signed in.", data=_state_payload())


def _cmd_logout(_payload: dict[str, Any]) -> Result:
    p = _account_path()
    for path in (p, p.with_suffix(".meta.json")):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            return Result(False, f"Could not clear credentials: {exc}")
    return Result(True, "Signed out.", data=_state_payload())


def _resolve_python() -> str:
    candidates = [
        os.environ.get("BALEOBALA_PYTHON"),
        str(Path("/Users/emad/IdeaProjects/baleobala/.venv/bin/python")),
        shutil.which("python3"),
        "/usr/bin/python3",
    ]
    for cand in candidates:
        if cand and Path(cand).exists():
            return cand
    return sys.executable


def _cmd_proxy_start(payload: dict[str, Any]) -> Result:
    if _proxy_running():
        return Result(True, "Proxy already running.", data=_state_payload())
    if not _logged_in():
        return Result(False, "Sign in first.", data=_state_payload())

    settings = ProxySettings.load()
    if "listen_port" in payload or "network_service" in payload:
        _cmd_save_settings(payload)
        settings = ProxySettings.load()

    _RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    python_bin = _resolve_python()
    cmd = [
        python_bin, "-u", "-m", "baleobala.cli", "bale-proxy", "system",
        "--transport", settings.transport,
        "--bale-jwt-file", str(_account_path()),
        "--peer-id", settings.relay_peer_id,
        "--listen-host", settings.listen_host,
        "--listen-port", str(settings.listen_port),
        "--service", settings.network_service,
        "--proxy-ready-timeout", "60",
        "--ws-ssl-no-verify",
    ]
    psk = Path.home() / ".baleobala" / "baleobala-vpn.psk"
    if psk.exists():
        cmd.extend(["--proxy-secret-file", str(psk)])

    log_fh = open(_LOG_FILE, "ab")
    try:
        proc = subprocess.Popen(  # noqa: S603
            cmd,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:  # noqa: BLE001
        log_fh.close()
        return Result(False, f"Could not launch proxy: {exc}", data=_state_payload())
    finally:
        try:
            log_fh.close()
        except Exception:
            pass

    _PID_FILE.write_text(str(proc.pid))
    _write_meta({
        "pid": proc.pid,
        "started_at": time.time(),
        "cmd": cmd,
    })
    # Wait until either the macOS system proxy is verifiably set
    # (networksetup reports Enabled: Yes) or the subprocess died. The
    # previous 0.5s sleep + pid-alive check would happily return
    # success while the underlying call was still being placed, then
    # CallNotApproved a few seconds later — leaving the UI claiming
    # "Connected" with no proxy applied.
    deadline = time.time() + 30.0
    last_state: dict[str, str] = {"Enabled": "No"}
    while time.time() < deadline:
        if not _pid_alive(proc.pid):
            return Result(
                False,
                "Proxy exited before macOS proxy was applied. Check the log "
                f"({_LOG_FILE}).",
                data=_state_payload(),
            )
        last_state = _socks_proxy_state(settings.network_service)
        if last_state.get("Enabled") == "Yes":
            return Result(True, "Proxy started; macOS SOCKS proxy active.", data=_state_payload())
        time.sleep(0.5)
    # Timed out waiting for system proxy to flip on. Tear down so the
    # caller doesn't end up with a zombie subprocess and stale PID.
    _kill_pid(proc.pid)
    try:
        _PID_FILE.unlink()
    except FileNotFoundError:
        pass
    _disable_all_proxies_everywhere()
    return Result(
        False,
        "Proxy started but macOS networksetup never reported Enabled: Yes "
        "within 30 s. Check the log for CallNotApproved or transport errors.",
        data=_state_payload(),
    )


def _cmd_proxy_stop(_payload: dict[str, Any]) -> Result:
    pid = _read_pid()
    if pid is not None and _pid_alive(pid):
        _kill_pid(pid)
    try:
        _PID_FILE.unlink()
    except FileNotFoundError:
        pass
    # Always sweep every network service, not just the configured one.
    # If the user changed network_service in settings between start and
    # stop, the original service would otherwise be left with our SOCKS
    # proxy still wired up.
    _disable_all_proxies_everywhere()
    _write_meta({"stopped_at": time.time()})
    return Result(True, "Proxy stopped.", data=_state_payload())


COMMANDS = {
    "status": _cmd_status,
    "saveSettings": _cmd_save_settings,
    "listServices": _cmd_list_services,
    "authStart": _cmd_auth_start,
    "authVerify": _cmd_auth_verify,
    "logout": _cmd_logout,
    "proxyStart": _cmd_proxy_start,
    "proxyStop": _cmd_proxy_stop,
}


def handle(request: dict[str, Any]) -> Result:
    command = str(request.get("command", "")).strip()
    payload = request.get("payload") if isinstance(request.get("payload"), dict) else {}
    handler = COMMANDS.get(command)
    if handler is None:
        return Result(False, f"Unknown command: {command}")
    try:
        return handler(payload)
    except Exception as exc:  # noqa: BLE001
        return Result(False, f"Helper error: {exc}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="baleobala-proxy-helper")
    parser.add_argument("--once", action="store_true", help="read a single JSON request from stdin")
    args = parser.parse_args(argv)

    if args.once:
        raw = sys.stdin.read().strip()
        try:
            req = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            sys.stdout.write(json.dumps({"ok": False, "message": f"Bad JSON: {exc}"}))
            return 1
        result = handle(req if isinstance(req, dict) else {})
        sys.stdout.write(json.dumps(result.to_dict()))
        return 0

    # Interactive line-delimited mode.
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            sys.stdout.write(json.dumps({"ok": False, "message": f"Bad JSON: {exc}"}) + "\n")
            sys.stdout.flush()
            continue
        result = handle(req if isinstance(req, dict) else {})
        sys.stdout.write(json.dumps(result.to_dict()) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
