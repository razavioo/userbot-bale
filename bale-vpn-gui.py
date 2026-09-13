import json
import os
import shlex
import shutil
import signal
import subprocess
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from pathlib import Path

# Premium-minimal palette (Linear/Vercel-inspired, dark only)
BG_BASE = "#08090C"
BG_SURFACE = "#101114"
BG_ELEVATED = "#16181D"
BORDER_SUBTLE = "#1C1F26"
BORDER_STRONG = "#262A33"
ACCENT = "#7C5CFF"
ACCENT_HOVER = "#8E73FF"
ACCENT_PRESSED = "#6948E8"
ACCENT_SOFT = "#1A1530"
SUCCESS = "#3FE0A0"
SUCCESS_SOFT = "#0F2A22"
WARNING = "#FFC066"
WARNING_SOFT = "#2A2014"
ERROR = "#FF5C7C"
ERROR_SOFT = "#2A1620"
TEXT_PRIMARY = "#F4F5F8"
TEXT_SECONDARY = "#9BA1AE"
TEXT_TERTIARY = "#5C606A"

# Aliases used downstream
BG_COLOR = BG_BASE
CARD_BG = BG_SURFACE
ACCENT_COLOR = ACCENT
SUCCESS_COLOR = SUCCESS
WARNING_COLOR = WARNING
ERROR_COLOR = ERROR
TEXT_COLOR = TEXT_PRIMARY
TEXT_DIM = TEXT_TERTIARY
TEXT_MUTED = TEXT_SECONDARY
BUTTON_DIM = BG_ELEVATED
STATUS_CONNECT_TIMEOUT = 40

# Glyphs that render on every platform (Linux/macOS/Windows) — no SF Symbol PUA.
GLYPH_OFFLINE = "⏻"   # ⏻ power symbol
GLYPH_CONNECTING = "◐"  # ◐ half circle
GLYPH_CONNECTED = "✔"   # ✔ heavy checkmark
GLYPH_WARNING = "⚠"     # ⚠ warning
GLYPH_RELAY = "⦿"       # ⦿ bullseye
GLYPH_ENDPOINT = "◇"    # ◇ diamond
GLYPH_TRANSPORT = "⇄"   # ⇄ arrows
GLYPH_HEALTH = "◎"      # ◎ circle


def _pick_font(candidates, size, weight="normal"):
    """Return the first family from candidates that the system has, else default."""
    available = set(tkfont.families())
    for fam in candidates:
        if fam in available:
            return (fam, size, weight)
    return ("TkDefaultFont", size, weight)


class BaleVPNApp:
    def __init__(self, root):
        self.root = root
        self.root.title("userbot-bale")
        self.root.geometry("470x720")
        self.root.configure(bg=BG_BASE)
        self.root.minsize(420, 680)

        # Pre-resolve font triples so we don't keep guessing at draw time.
        self.f_display = _pick_font(("Inter Display", "Inter", "SF Pro Display", "Segoe UI", "Helvetica Neue"), 22, "bold")
        self.f_ui = _pick_font(("Inter", "SF Pro Text", "Segoe UI", "Helvetica Neue"), 11)
        self.f_ui_bold = _pick_font(("Inter", "SF Pro Text", "Segoe UI", "Helvetica Neue"), 11, "bold")
        self.f_eyebrow = _pick_font(("Inter", "SF Pro Text", "Segoe UI", "Helvetica Neue"), 8, "bold")
        self.f_chip = _pick_font(("Inter", "SF Pro Text", "Segoe UI", "Helvetica Neue"), 9, "bold")
        self.f_mono = _pick_font(("JetBrains Mono", "SF Mono", "Menlo", "Consolas"), 10)
        self.f_icon_lg = _pick_font(("Inter", "SF Pro Display", "Segoe UI Symbol", "DejaVu Sans"), 56)
        self.f_icon_sm = _pick_font(("Inter", "SF Pro Display", "Segoe UI Symbol", "DejaVu Sans"), 14)

        self.process = None
        self.phase = "disconnected"
        self.started_at = None
        self.connecting_started_at = None
        self.connected_marker_seen = False
        self.last_runtime_line = ""
        self._lock = threading.Lock()

        self.settings = self._load_settings()

        self._setup_styles()
        self._setup_ui()
        self._refresh_cards()
        self._update_loop()

    def _setup_styles(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        # Combobox — replaces the old OptionMenu for a cleaner look.
        style.configure(
            "Premium.TCombobox",
            fieldbackground=BG_ELEVATED,
            background=BG_ELEVATED,
            foreground=TEXT_PRIMARY,
            bordercolor=BORDER_STRONG,
            lightcolor=BORDER_STRONG,
            darkcolor=BORDER_STRONG,
            arrowcolor=TEXT_SECONDARY,
            relief="flat",
            padding=(10, 6, 10, 6),
        )
        style.map(
            "Premium.TCombobox",
            fieldbackground=[("readonly", BG_ELEVATED), ("focus", BG_ELEVATED)],
            foreground=[("readonly", TEXT_PRIMARY)],
            bordercolor=[("focus", ACCENT)],
            lightcolor=[("focus", ACCENT)],
            darkcolor=[("focus", ACCENT)],
        )
        self.root.option_add("*TCombobox*Listbox.background", BG_ELEVATED)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT_PRIMARY)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")
        self.root.option_add("*TCombobox*Listbox.borderWidth", 0)
        self.root.option_add("*TCombobox*Listbox.relief", "flat")

    def _load_settings(self):
        home = Path.home()
        secret_dir = home / ".config" / "userbot-bale" / "secrets"
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
            "mode": os.environ.get("BALE_GUI_MODE", "proxy"),
            "profile_id": os.environ.get("BALE_VPN_PROFILE_ID", ""),
            "tun_name": os.environ.get("BALE_TUN_NAME", "vpn0"),
            "tun_addr": os.environ.get("BALE_TUN_ADDR", "10.77.0.2/24"),
            "tun_mtu": int(os.environ.get("BALE_TUN_MTU", "1400")),
            "jwt_path": os.environ.get(
                "BALE_PROXY_JWT_PATH",
                str(fallback_secret),
            ),
            "psk": os.environ.get("BALE_PROXY_PSK", "N9OcrcH_lJXU241OqKXL0SxP1YAWtALTdUitTLojlko"),
            "listen_host": "127.0.0.1",
        }
        cfg_path = home / ".config" / "userbot-bale" / "proxy-gui.json"
        if cfg_path.exists():
            try:
                file_data = json.loads(cfg_path.read_text(encoding="utf-8"))
                if isinstance(file_data, dict):
                    defaults.update(file_data)
            except Exception:
                pass
        return defaults

    def _hairline(self, parent, color=BORDER_SUBTLE):
        """A 1-pixel separator line."""
        return tk.Frame(parent, bg=color, height=1, bd=0, highlightthickness=0)

    def _setup_ui(self):
        # Title bar
        title_frame = tk.Frame(self.root, bg=BG_BASE, padx=24, pady=18)
        title_frame.pack(fill="x")
        wordmark = tk.Frame(title_frame, bg=BG_BASE)
        wordmark.pack(side="left")
        tk.Label(
            wordmark,
            text="userbot-bale",
            bg=BG_BASE,
            fg=TEXT_PRIMARY,
            font=self.f_ui_bold,
        ).pack(side="left")
        tk.Label(
            wordmark,
            text="  proxy",
            bg=BG_BASE,
            fg=TEXT_TERTIARY,
            font=self.f_ui,
        ).pack(side="left")
        self.state_chip = tk.Label(
            title_frame,
            text="OFFLINE",
            bg=BG_ELEVATED,
            fg=TEXT_SECONDARY,
            font=self.f_chip,
            padx=10,
            pady=4,
            bd=0,
            highlightthickness=1,
            highlightbackground=BORDER_STRONG,
            highlightcolor=BORDER_STRONG,
        )
        self.state_chip.pack(side="right")

        self._hairline(self.root).pack(fill="x")

        self.main_container = tk.Frame(self.root, bg=BG_BASE)
        self.main_container.pack(fill="both", expand=True, padx=24, pady=(8, 0))

        # Mode row
        mode_row = tk.Frame(self.main_container, bg=BG_BASE)
        mode_row.pack(fill="x", pady=(12, 4))
        tk.Label(
            mode_row,
            text="MODE",
            bg=BG_BASE,
            fg=TEXT_TERTIARY,
            font=self.f_eyebrow,
        ).pack(side="left")
        self.mode_var = tk.StringVar(value=str(self.settings.get("mode", "proxy")))
        self.mode_menu = ttk.Combobox(
            mode_row,
            textvariable=self.mode_var,
            values=("proxy", "tunnel"),
            state="readonly",
            width=10,
            style="Premium.TCombobox",
        )
        self.mode_menu.bind("<<ComboboxSelected>>", lambda e: self._on_mode_changed(self.mode_var.get()))
        self.mode_menu.pack(side="right")

        # Hero status block
        hero = tk.Frame(self.main_container, bg=BG_BASE)
        hero.pack(fill="x", pady=(28, 8))

        # Outer canvas acts as a soft accent halo behind the status glyph.
        self.halo = tk.Canvas(
            hero, width=160, height=160, bg=BG_BASE, bd=0, highlightthickness=0
        )
        self.halo.pack()
        self._draw_halo(BORDER_STRONG)

        self.phase_label = tk.Label(
            self.main_container,
            text="Disconnected",
            bg=BG_BASE,
            fg=TEXT_PRIMARY,
            font=self.f_display,
        )
        self.phase_label.pack(pady=(18, 4))

        self.timer_label = tk.Label(
            self.main_container,
            text="——:——:——",
            bg=BG_BASE,
            fg=TEXT_TERTIARY,
            font=self.f_mono,
        )
        self.timer_label.pack()

        # Hero action button — flat, generous padding, accent fill.
        self.action_btn = tk.Button(
            self.main_container,
            text="Connect",
            command=self.toggle_connection,
            bg=ACCENT,
            fg="#FFFFFF",
            font=self.f_ui_bold,
            activebackground=ACCENT_PRESSED,
            activeforeground="#FFFFFF",
            padx=40,
            pady=14,
            borderwidth=0,
            highlightthickness=0,
            cursor="hand2",
            relief="flat",
        )
        self.action_btn.pack(pady=24, fill="x")
        self.action_btn.bind("<Enter>", lambda e: self._btn_hover(True))
        self.action_btn.bind("<Leave>", lambda e: self._btn_hover(False))

        # Info card stack
        self.cards_frame = tk.Frame(self.main_container, bg=BG_BASE)
        self.cards_frame.pack(fill="x", pady=(4, 0))

        self.card_peer = self._create_info_card(self.cards_frame, "Relay Peer", "-", GLYPH_RELAY)
        self.card_endpoint = self._create_info_card(self.cards_frame, "Endpoint", "-", GLYPH_ENDPOINT)
        self.card_transport = self._create_info_card(self.cards_frame, "Transport", "-", GLYPH_TRANSPORT)
        self.card_health = self._create_info_card(self.cards_frame, "Health", "Idle", GLYPH_HEALTH)

        # Status bar
        self._hairline(self.root).pack(fill="x", side="bottom")
        self.status_bar = tk.Label(
            self.root,
            text="Ready to connect",
            bg=BG_BASE,
            fg=TEXT_SECONDARY,
            font=self.f_ui,
            anchor="w",
            padx=20,
            pady=10,
        )
        self.status_bar.pack(side="bottom", fill="x")

    def _draw_halo(self, ring_color, glyph=GLYPH_OFFLINE, glyph_color=TEXT_TERTIARY):
        """Render the soft ring + centred glyph on the hero canvas."""
        c = self.halo
        c.delete("all")
        # Multi-pass ring to fake a glow without compositing.
        for radius, color in (
            (78, BORDER_SUBTLE),
            (66, ring_color),
        ):
            c.create_oval(80 - radius, 80 - radius, 80 + radius, 80 + radius,
                          outline=color, width=1)
        c.create_text(80, 84, text=glyph, fill=glyph_color, font=self.f_icon_lg)

    def _btn_hover(self, hovering: bool):
        # Hover state mirrors the accent token without dancing with ACTIVE_BG.
        if self.phase == "disconnected":
            self.action_btn.config(bg=ACCENT_HOVER if hovering else ACCENT)
        elif self.phase in ("connecting", "connected", "unhealthy"):
            self.action_btn.config(bg="#FF7E96" if hovering else ERROR)

    def _create_info_card(self, parent, title, value, icon):
        # Wrap in a border frame to fake a 1px hairline outline.
        outer = tk.Frame(parent, bg=BORDER_SUBTLE)
        outer.pack(fill="x", pady=4)
        card = tk.Frame(outer, bg=BG_SURFACE, padx=14, pady=12)
        card.pack(fill="x", padx=1, pady=1)

        icon_holder = tk.Frame(card, bg=BG_SURFACE)
        icon_holder.pack(side="left", padx=(0, 12))
        tk.Label(
            icon_holder,
            text=icon,
            bg=BG_SURFACE,
            fg=ACCENT,
            font=self.f_icon_sm,
        ).pack()

        info_v = tk.Frame(card, bg=BG_SURFACE)
        info_v.pack(side="left", fill="x", expand=True)
        tk.Label(
            info_v,
            text=title.upper(),
            bg=BG_SURFACE,
            fg=TEXT_TERTIARY,
            font=self.f_eyebrow,
        ).pack(anchor="w")
        val_label = tk.Label(
            info_v,
            text=value,
            bg=BG_SURFACE,
            fg=TEXT_PRIMARY,
            font=self.f_ui,
        )
        val_label.pack(anchor="w", pady=(2, 0))
        return val_label

    def _refresh_cards(self):
        mode = str(self.settings.get("mode", "proxy"))
        if mode == "proxy":
            self.card_peer.config(text=str(self.settings.get("peer_id") or "-"))
            self.card_endpoint.config(text=f"{self.settings.get('listen_host', '127.0.0.1')}:{self.settings.get('port', 1080)}")
            self.card_transport.config(text=f"proxy/{self.settings.get('transport') or 'dc'}")
        else:
            self.card_peer.config(text=str(self.settings.get("profile_id") or "active-profile"))
            self.card_endpoint.config(text="vpn0 (linux-tun)")
            self.card_transport.config(text="tunnel/linux-tun")

    def _on_mode_changed(self, selected):
        if self.phase in {"connecting", "connected"}:
            self.status_bar.config(text="Disconnect current session before switching mode.")
            self.mode_var.set(str(self.settings.get("mode", "proxy")))
            return
        self.settings["mode"] = str(selected)
        self._refresh_cards()
        if selected == "proxy":
            self.status_bar.config(text="Proxy mode selected.")
        else:
            self.status_bar.config(text="Tunnel mode selected.")

    def _update_loop(self):
        if self.phase == "connected" and self.started_at:
            elapsed = int(time.time() - self.started_at)
            h = elapsed // 3600
            m = (elapsed % 3600) // 60
            s = elapsed % 60
            self.timer_label.config(text=f"{h:02d}:{m:02d}:{s:02d}", fg=TEXT_SECONDARY)
        else:
            self.timer_label.config(text="——:——:——", fg=TEXT_TERTIARY)

        if self.phase == "disconnected":
            self._draw_halo(BORDER_STRONG, GLYPH_OFFLINE, TEXT_TERTIARY)
            self.phase_label.config(text="Disconnected", fg=TEXT_PRIMARY)
            self.action_btn.config(text="Connect", bg=ACCENT, activebackground=ACCENT_PRESSED)
            self.state_chip.config(text="OFFLINE", bg=BG_ELEVATED, fg=TEXT_SECONDARY,
                                   highlightbackground=BORDER_STRONG)
            self.card_health.config(text="Idle")
        elif self.phase == "connecting":
            self._draw_halo(ACCENT, GLYPH_CONNECTING, ACCENT)
            self.phase_label.config(text="Connecting…", fg=ACCENT)
            self.action_btn.config(text="Cancel", bg=ERROR, activebackground="#E64868")
            self.state_chip.config(text="CONNECTING", bg=ACCENT_SOFT, fg=ACCENT,
                                   highlightbackground=ACCENT)
            self.card_health.config(text="Dialing relay")
            if self.connecting_started_at and (time.time() - self.connecting_started_at > STATUS_CONNECT_TIMEOUT):
                self.phase = "unhealthy"
                self.status_bar.config(text="Connection timeout. Check peer/jwt/transport.")
        elif self.phase == "connected":
            self._draw_halo(SUCCESS, GLYPH_CONNECTED, SUCCESS)
            self.phase_label.config(text="Protected", fg=SUCCESS)
            self.action_btn.config(text="Disconnect", bg=ERROR, activebackground="#E64868")
            self.state_chip.config(text="CONNECTED", bg=SUCCESS_SOFT, fg=SUCCESS,
                                   highlightbackground=SUCCESS)
            self.card_health.config(text="Healthy tunnel")
        elif self.phase == "unhealthy":
            self._draw_halo(WARNING, GLYPH_WARNING, WARNING)
            self.phase_label.config(text="Needs Attention", fg=WARNING)
            self.action_btn.config(text="Disconnect", bg=ERROR, activebackground="#E64868")
            self.state_chip.config(text="DEGRADED", bg=WARNING_SOFT, fg=WARNING,
                                   highlightbackground=WARNING)
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

    def _build_proxy_command(self):
        cli_bin = os.environ.get("USERBOT_BALE_BIN")
        if cli_bin:
            prefix = shlex.split(cli_bin)
        else:
            venv_cli = Path(__file__).resolve().parent / ".venv" / "bin" / "userbot-bale"
            system_cli = shutil.which("userbot-bale")
            if venv_cli.exists():
                prefix = [str(venv_cli)]
            elif system_cli:
                prefix = [system_cli]
            else:
                prefix = [os.environ.get("PYTHON", "python3"), "-m", "userbot_bale.cli"]
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

    def _build_tunnel_command(self):
        cli_bin = os.environ.get("USERBOT_BALE_BIN")
        if cli_bin:
            prefix = shlex.split(cli_bin)
        else:
            venv_cli = Path(__file__).resolve().parent / ".venv" / "bin" / "userbot-bale"
            system_cli = shutil.which("userbot-bale")
            if venv_cli.exists():
                prefix = [str(venv_cli)]
            elif system_cli:
                prefix = [system_cli]
            else:
                prefix = [os.environ.get("PYTHON", "python3"), "-m", "userbot_bale.cli"]
        cmd = [
            *prefix,
            "tunnel",
            "up",
            "--bale-jwt-file",
            str(self.settings["jwt_path"]),
            "--peer-id",
            str(self.settings["peer_id"]),
            "--tun",
            str(self.settings.get("tun_name", "vpn0")),
            "--tun-addr",
            str(self.settings.get("tun_addr", "10.77.0.2/24")),
            "--tun-mtu",
            str(self.settings.get("tun_mtu", 1400)),
            "--transport",
            str(self.settings["transport"]),
            "--identity",
            "userbot-bale-vpn-gui",
        ]
        psk = str(self.settings.get("psk") or "").strip()
        if psk:
            cmd.extend(["--psk", psk])
        return cmd

    def _command_env(self):
        env = os.environ.copy()
        workspace_root = str(Path(__file__).resolve().parent)
        src_path = str(Path(workspace_root) / "src")
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{src_path}:{existing}" if existing else src_path
        return env

    def start_proxy(self):
        mode = str(self.settings.get("mode", "proxy"))
        if mode == "proxy":
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
            cmd = self._build_proxy_command()
        else:
            peer_id = str(self.settings.get("peer_id", "")).strip()
            jwt_path = Path(str(self.settings.get("jwt_path", ""))).expanduser()
            if not peer_id:
                self.status_bar.config(text="peer_id is empty. Tunnel mode requires peer_id.")
                self.phase = "unhealthy"
                return
            if not jwt_path.exists():
                self.status_bar.config(text=f"JWT file not found: {jwt_path}")
                self.phase = "unhealthy"
                return
            self.settings["jwt_path"] = str(jwt_path)
            cmd = self._build_tunnel_command()
        self.phase = "connecting"
        self.connecting_started_at = time.time()
        self.connected_marker_seen = False
        self.last_runtime_line = ""
        self.status_bar.config(text="Dialing relay…" if mode == "proxy" else "Bringing linux-tun up…")
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
                    if "linux-tun backend active" in line:
                        self.root.after(0, self.on_connected)
                    if "tunnel_up=" in line:
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
        if str(self.settings.get("mode", "proxy")) == "proxy":
            self.set_system_proxy(True)
            self.status_bar.config(text="SOCKS proxy enabled for this desktop session.")
        else:
            self.status_bar.config(text="linux-tun connected (system tunnel active).")

    def on_disconnected(self, code):
        with self._lock:
            previous_phase = self.phase
        self.phase = "disconnected"
        self.connecting_started_at = None
        self.started_at = None
        if str(self.settings.get("mode", "proxy")) == "proxy":
            self.set_system_proxy(False)
        if previous_phase == "connecting" and code != 0:
            self.phase = "unhealthy"
            detail = self.last_runtime_line or "no runtime details"
            self.status_bar.config(text=f"Failed to connect (exit={code}) — {detail}")
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
