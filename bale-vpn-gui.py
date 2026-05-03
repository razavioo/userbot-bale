import json
import os
import shlex
import shutil
import signal
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path

# Colors close to macOS dark system UI
BG_COLOR = "#0f0f0f"
CARD_BG = "#1a1a1a"
ACCENT_COLOR = "#007aff"
SUCCESS_COLOR = "#28cd41"
WARNING_COLOR = "#ffcc00"
ERROR_COLOR = "#ff3b30"
TEXT_COLOR = "#ffffff"
TEXT_DIM = "#888888"
TEXT_MUTED = "#a3a3a3"
BUTTON_DIM = "#2c2c2e"
STATUS_CONNECT_TIMEOUT = 40

class BaleVPNApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Baleobala Proxy")
        self.root.geometry("470x690")
        self.root.configure(bg=BG_COLOR)
        
        self.process = None
        self.phase = "disconnected"
        self.started_at = None
        self.connecting_started_at = None
        self.connected_marker_seen = False
        self.last_runtime_line = ""
        self._lock = threading.Lock()
        
        self.settings = self._load_settings()
        
        self._setup_ui()
        self._refresh_cards()
        self._update_loop()
    
    def _load_settings(self):
        home = Path.home()
        secret_dir = home / ".config" / "baleobala" / "secrets"
        preferred_secret = secret_dir / "77dcf9ee09cec7c1.secret"
        fallback_secret = preferred_secret
        if not preferred_secret.exists() and secret_dir.exists():
            candidates = sorted(
                [p for p in secret_dir.glob("*.secret") if p.is_file()],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if candidates:
                fallback_secret = candidates[0]
        defaults = {
            "peer_id": os.environ.get("BALE_PROXY_PEER_ID", "423217348"),
            "port": int(os.environ.get("BALE_PROXY_PORT", "1080")),
            "transport": os.environ.get("BALE_PROXY_TRANSPORT", "dc"),
            "jwt_path": os.environ.get(
                "BALE_PROXY_JWT_PATH",
                str(fallback_secret),
            ),
            "psk": os.environ.get("BALE_PROXY_PSK", "N9OcrcH_lJXU241OqKXL0SxP1YAWtALTdUitTLojlko"),
            "listen_host": "127.0.0.1",
        }
        cfg_path = home / ".config" / "baleobala" / "proxy-gui.json"
        if cfg_path.exists():
            try:
                file_data = json.loads(cfg_path.read_text(encoding="utf-8"))
                if isinstance(file_data, dict):
                    defaults.update(file_data)
            except Exception:
                pass
        return defaults

    def _setup_ui(self):
        title_frame = tk.Frame(self.root, bg=BG_COLOR, padx=22, pady=15)
        title_frame.pack(fill="x")
        tk.Label(
            title_frame,
            text="Baleobala Proxy",
            bg=BG_COLOR,
            fg="#d1d1d6",
            font=("SF Pro Text", 12, "bold"),
        ).pack(side="left")
        self.state_chip = tk.Label(
            title_frame,
            text="OFFLINE",
            bg=BUTTON_DIM,
            fg=TEXT_MUTED,
            font=("SF Pro Text", 9, "bold"),
            padx=10,
            pady=3,
        )
        self.state_chip.pack(side="right")

        self.main_container = tk.Frame(self.root, bg=BG_COLOR)
        self.main_container.pack(fill="both", expand=True, padx=24)

        self.status_icon = tk.Label(
            self.main_container,
            text="􀙇",
            bg=BG_COLOR,
            fg=TEXT_DIM,
            font=("SF Pro", 58),
        )
        self.status_icon.pack(pady=30)

        self.phase_label = tk.Label(
            self.main_container,
            text="Disconnected",
            bg=BG_COLOR,
            fg=TEXT_COLOR,
            font=("SF Pro Display", 20, "bold"),
        )
        self.phase_label.pack()
        
        self.timer_label = tk.Label(
            self.main_container,
            text="--:--:--",
            bg=BG_COLOR,
            fg=TEXT_DIM,
            font=("SF Mono", 11),
        )
        self.timer_label.pack(pady=5)

        self.action_btn = tk.Button(
            self.main_container,
            text="Connect",
            command=self.toggle_connection,
            bg=ACCENT_COLOR,
            fg="#fff",
            font=("SF Pro Text", 13, "bold"),
            activebackground="#005ecb",
            activeforeground="#fff",
            padx=40,
            pady=11,
            borderwidth=0,
            cursor="hand2",
        )
        self.action_btn.pack(pady=30, fill="x")

        self.cards_frame = tk.Frame(self.main_container, bg=BG_COLOR)
        self.cards_frame.pack(fill="x", pady=10)
        
        self.card_peer = self._create_info_card(self.cards_frame, "Relay Peer", "-", "􀤆")
        self.card_endpoint = self._create_info_card(self.cards_frame, "Endpoint", "-", "􀙇")
        self.card_transport = self._create_info_card(self.cards_frame, "Transport", "-", "􀊫")
        self.card_health = self._create_info_card(self.cards_frame, "Health", "Idle", "􀙥")

        self.status_bar = tk.Label(
            self.root,
            text="Ready to connect",
            bg="#151515",
            fg="#7d7d7d",
            font=("SF Pro Text", 10),
            anchor="w",
            padx=15,
            pady=6,
        )
        self.status_bar.pack(side="bottom", fill="x")

    def _create_info_card(self, parent, title, value, icon):
        card = tk.Frame(parent, bg=CARD_BG, padx=12, pady=10)
        card.pack(fill="x", pady=4)
        tk.Label(card, text=icon, bg=CARD_BG, fg="#6a6a6d", font=("SF Pro", 13)).pack(side="left")
        info_v = tk.Frame(card, bg=CARD_BG)
        info_v.pack(side="left", padx=10)
        tk.Label(info_v, text=title.upper(), bg=CARD_BG, fg="#6a6a6d", font=("SF Pro Text", 8, "bold")).pack(anchor="w")
        val_label = tk.Label(info_v, text=value, bg=CARD_BG, fg="#e5e5ea", font=("SF Pro Text", 11))
        val_label.pack(anchor="w")
        return val_label
    
    def _refresh_cards(self):
        self.card_peer.config(text=str(self.settings.get("peer_id") or "-"))
        self.card_endpoint.config(text=f"{self.settings.get('listen_host', '127.0.0.1')}:{self.settings.get('port', 1080)}")
        self.card_transport.config(text=str(self.settings.get("transport") or "dc"))

    def _update_loop(self):
        if self.phase == "connected" and self.started_at:
            elapsed = int(time.time() - self.started_at)
            h = elapsed // 3600
            m = (elapsed % 3600) // 60
            s = elapsed % 60
            self.timer_label.config(text=f"{h:02d}:{m:02d}:{s:02d}")
        else:
            self.timer_label.config(text="--:--:--")

        if self.phase == "disconnected":
            self.status_icon.config(text="􀙇", fg=TEXT_DIM)
            self.phase_label.config(text="Disconnected", fg=TEXT_COLOR)
            self.action_btn.config(text="Connect", bg=ACCENT_COLOR)
            self.state_chip.config(text="OFFLINE", bg=BUTTON_DIM, fg=TEXT_MUTED)
            self.card_health.config(text="Idle")
        elif self.phase == "connecting":
            self.status_icon.config(text="􀐊", fg=ACCENT_COLOR)
            self.phase_label.config(text="Connecting...", fg=ACCENT_COLOR)
            self.action_btn.config(text="Cancel", bg=ERROR_COLOR)
            self.state_chip.config(text="CONNECTING", bg="#11375d", fg="#9fd0ff")
            self.card_health.config(text="Dialing relay")
            if self.connecting_started_at and (time.time() - self.connecting_started_at > STATUS_CONNECT_TIMEOUT):
                self.phase = "unhealthy"
                self.status_bar.config(text="Connection timeout. Check peer/jwt/transport.")
        elif self.phase == "connected":
            self.status_icon.config(text="􀎡", fg=SUCCESS_COLOR)
            self.phase_label.config(text="Protected", fg=SUCCESS_COLOR)
            self.action_btn.config(text="Disconnect", bg=ERROR_COLOR)
            self.state_chip.config(text="CONNECTED", bg="#12381e", fg="#98f5aa")
            self.card_health.config(text="Healthy tunnel")
        elif self.phase == "unhealthy":
            self.status_icon.config(text="􀇿", fg=WARNING_COLOR)
            self.phase_label.config(text="Needs Attention", fg=WARNING_COLOR)
            self.action_btn.config(text="Disconnect", bg=ERROR_COLOR)
            self.state_chip.config(text="DEGRADED", bg="#3d3100", fg="#ffea8c")
            self.card_health.config(text="Started but not stable")

        self.root.after(1000, self._update_loop)

    def set_system_proxy(self, enable=True):
        mode = "manual" if enable else "none"
        port = str(self.settings["port"])
        host = self.settings.get("listen_host", "127.0.0.1")
        no_proxy = "localhost,127.0.0.1,::1"
        commands = [
            ["gsettings", "set", "org.gnome.system.proxy", "mode", mode],
        ]
        if enable:
            commands.extend(
                [
                    ["gsettings", "set", "org.gnome.system.proxy.socks", "host", host],
                    ["gsettings", "set", "org.gnome.system.proxy.socks", "port", port],
                    ["gsettings", "set", "org.gnome.system.proxy", "ignore-hosts", f"['localhost','127.0.0.1','::1']"],
                ]
            )
        else:
            commands.append(
                ["gsettings", "set", "org.gnome.system.proxy", "ignore-hosts", "[]"]
            )
        try:
            for cmd in commands:
                subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        # Fallback for non-GNOME applications that honor environment variables.
        if enable:
            os.environ["ALL_PROXY"] = f"socks5://{host}:{port}"
            os.environ["all_proxy"] = os.environ["ALL_PROXY"]
            os.environ["NO_PROXY"] = no_proxy
            os.environ["no_proxy"] = no_proxy
        else:
            for key in ("ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy"):
                os.environ.pop(key, None)

    def toggle_connection(self):
        if self.phase == "disconnected":
            self.start_proxy()
        else:
            self.stop_proxy()

    def _build_command(self):
        cli_bin = os.environ.get("BALEOBALA_BIN")
        if cli_bin:
            prefix = shlex.split(cli_bin)
        else:
            venv_cli = Path(__file__).resolve().parent / ".venv" / "bin" / "baleobala"
            system_cli = shutil.which("baleobala")
            if venv_cli.exists():
                prefix = [str(venv_cli)]
            elif system_cli:
                prefix = [system_cli]
            else:
                prefix = [os.environ.get("PYTHON", "python3"), "-m", "baleobala.cli"]
        cmd = [
            *prefix,
            "bale-proxy",
            "client",
            "--bale-jwt-file",
            str(self.settings["jwt_path"]),
            "--peer-id",
            str(self.settings["peer_id"]),
            "--listen-host",
            str(self.settings.get("listen_host", "127.0.0.1")),
            "--listen-port",
            str(self.settings["port"]),
            "--transport",
            str(self.settings["transport"]),
        ]
        psk = str(self.settings.get("psk") or "").strip()
        if psk:
            cmd.extend(["--proxy-secret", psk])
        return cmd

    def _command_env(self):
        env = os.environ.copy()
        workspace_root = str(Path(__file__).resolve().parent)
        src_path = str(Path(workspace_root) / "src")
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{src_path}:{existing}" if existing else src_path
        return env

    def start_proxy(self):
        peer_id = str(self.settings.get("peer_id", "")).strip()
        jwt_path = Path(str(self.settings.get("jwt_path", ""))).expanduser()
        if not peer_id:
            self.status_bar.config(text="peer_id is empty. Set BALE_PROXY_PEER_ID or config file.")
            self.phase = "unhealthy"
            return
        if not jwt_path.exists():
            self.status_bar.config(text=f"JWT file not found: {jwt_path}")
            self.phase = "unhealthy"
            return
        self.settings["jwt_path"] = str(jwt_path)
        cmd = self._build_command()
        self.phase = "connecting"
        self.connecting_started_at = time.time()
        self.connected_marker_seen = False
        self.last_runtime_line = ""
        self.status_bar.config(text="Dialing relay...")
        self.card_health.config(text="Waiting for relay")
        def run_proc():
            try:
                self.process = subprocess.Popen(
                    cmd,
                    env=self._command_env(),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    preexec_fn=os.setsid,
                )
                for line in iter(self.process.stdout.readline, ""):
                    line = line.strip()
                    if not line:
                        continue
                    self.last_runtime_line = line
                    if "proxy_listening" in line:
                        self.root.after(0, self.on_connected)
                    if "call_established" in line:
                        self.connected_marker_seen = True
                    if "transport_selected=" in line:
                        self.root.after(
                            0,
                            lambda text=line: self.card_transport.config(
                                text=text.split("=", 1)[1]
                            ),
                        )
                    if "livekit_peer_ready" in line:
                        self.root.after(0, lambda: self.card_health.config(text="Peer is ready"))
                    if "proxy_browser_error=" in line:
                        self.root.after(
                            0, lambda text=line: self.status_bar.config(text=text)
                        )
                self.process.stdout.close()
                rc = self.process.wait()
                self.root.after(0, lambda: self.on_disconnected(rc))
            except Exception:
                self.root.after(0, lambda: self.on_disconnected(-1))
        threading.Thread(target=run_proc, daemon=True).start()

    def on_connected(self):
        with self._lock:
            if self.phase not in {"connecting", "connected"}:
                return
        self.phase = "connected"
        self.connecting_started_at = None
        self.started_at = time.time()
        self.set_system_proxy(True)
        self.status_bar.config(text="SOCKS proxy enabled for this desktop session.")

    def on_disconnected(self, code):
        with self._lock:
            previous_phase = self.phase
        self.phase = "disconnected"
        self.connecting_started_at = None
        self.started_at = None
        self.set_system_proxy(False)
        if previous_phase == "connecting" and code != 0:
            self.phase = "unhealthy"
            detail = self.last_runtime_line or "no runtime details"
            self.status_bar.config(text=f"Failed to connect (exit={code}) - {detail}")
            self.card_health.config(text="Connection failed")
            return
        if previous_phase == "connected" and code != 0 and not self.connected_marker_seen:
            self.phase = "unhealthy"
            self.status_bar.config(text=f"Disconnected before stable session (exit={code}).")
            self.card_health.config(text="Unstable session")
            return
        self.status_bar.config(text=f"Disconnected (exit={code})")

    def stop_proxy(self):
        if self.process:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            except Exception:
                pass
            self.process = None
        self.on_disconnected(0)

    def on_closing(self):
        self.stop_proxy()
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = BaleVPNApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()
