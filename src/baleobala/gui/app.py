"""Main Qt window for the guided Bale sign-in and connection flow."""

from __future__ import annotations

import json
import logging
import time
import sys
import threading
from logging.handlers import RotatingFileHandler
from typing import Optional

from PySide6.QtCore import Qt, QThread
from PySide6.QtGui import QAction, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QColor, QPalette

from baleobala.control import ControlService
from baleobala.control.paths import data_dir
from baleobala.gui.theme import apply_theme
from baleobala.gui.workers import (
    ControlPlaneConnectWorker,
    QtLogHandler,
    StartSmsWorker,
    ValidateCodeWorker,
    StatusPoller,
    run_in_thread,
)
from baleobala.presentation import (
    Banner,
    connect_banner,
    connect_readiness,
    connect_timeline,
    friendly_error_message,
    validate_phone_input,
    validate_sms_code,
)

log = logging.getLogger(__name__)


def _configure_gui_logging() -> None:
    root = logging.getLogger()
    if getattr(root, "_baleobala_gui_logging", False):
        return

    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    stream = logging.StreamHandler(sys.stderr)
    stream.setLevel(logging.INFO)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    log_path = data_dir() / "gui.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(log_path, maxBytes=512_000, backupCount=3)
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
        root.info("gui logging to %s", log_path)
    except Exception as e:
        root.warning("failed to open gui log file %s: %s", log_path, e)

    def _excepthook(exc_type, exc, tb) -> None:
        root.critical("unhandled exception", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    def _thread_excepthook(args) -> None:  # noqa: ANN001
        root.critical(
            "unhandled thread exception in %s",
            args.thread.name if args.thread else "unknown",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook  # type: ignore[attr-defined]
    root._baleobala_gui_logging = True


def _section(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("section")
    return label


def _build_qr_pixmap(data: str, size: int = 220) -> QPixmap | None:
    try:
        import qrcode
    except Exception:
        return None

    image = qrcode.make(data).convert("RGB")
    width, height = image.size
    bytes_per_line = 3 * width
    qimage = QImage(image.tobytes(), width, height, bytes_per_line, QImage.Format.Format_RGB888).copy()
    pixmap = QPixmap.fromImage(qimage)
    return pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


def _humanize_transport(value: str) -> str:
    normalized = value.strip().lower()
    mapping = {
        "dc": "DC",
        "datachannel": "DC",
        "audio": "audio",
        "qr": "QR",
        "rpc": "RPC",
        "mtproto_rpc": "RPC",
        "saved transport": "saved transport",
    }
    return mapping.get(normalized, value or "unknown")


def _humanize_state(value: str) -> str:
    normalized = value.strip().lower()
    mapping = {
        "running": "connected",
        "ready": "connected",
        "connecting": "connecting",
        "recovering": "recovering",
        "stopping": "recovering",
        "stopped": "disconnected",
        "idle": "disconnected",
        "degraded": "recovering",
        "blocked": "disconnected",
    }
    return mapping.get(normalized, normalized or "disconnected")


def _format_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "n/a"
    seconds = int(round(seconds))
    if seconds <= 0:
        return "expired"
    minutes, rem = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if rem or not parts:
        parts.append(f"{rem}s")
    return "in " + " ".join(parts)


def _format_countdown(expires_at: str | float | None) -> str:
    if expires_at in {None, "", "none"}:
        return "n/a"
    try:
        remaining = float(expires_at) - time.time()
    except (TypeError, ValueError):
        return "n/a"
    return _format_duration(remaining)


def _gate_action_text(code: str) -> str:
    mapping = {
        "auth_expired": "Sign in again",
        "auth_missing": "Sign in again",
        "pairing_missing": "Create pairing",
        "credentials_expired": "Refresh credentials",
        "credentials_missing": "Sync credentials",
        "access_revoked": "Open pairing setup",
        "access_rejected": "Open pairing setup",
        "provisioning_pending": "Open pairing setup",
        "provisioning_missing": "Open pairing setup",
        "peer_assignment_missing": "Sync pairing",
    }
    return mapping.get(code, "Retry connect")


class ConnectionDashboard(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("dashboard-panel")
        layout = QFormLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setHorizontalSpacing(18)
        layout.setVerticalSpacing(8)

        self.connection_state = QLabel("disconnected")
        self.transport = QLabel("n/a")
        self.failover = QLabel("0")
        self.recovery = QLabel("n/a")
        self.session_id = QLabel("n/a")
        self.latency = QLabel("n/a")
        self.credential_expiry = QLabel("n/a")

        for label in (
            self.connection_state,
            self.transport,
            self.failover,
            self.recovery,
            self.session_id,
            self.latency,
            self.credential_expiry,
        ):
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        layout.addRow("Connection state", self.connection_state)
        layout.addRow("Selected transport", self.transport)
        layout.addRow("Failover count", self.failover)
        layout.addRow("Recovery state", self.recovery)
        layout.addRow("Carrier session ID", self.session_id)
        layout.addRow("Latency hint", self.latency)
        layout.addRow("Credential expiry", self.credential_expiry)

    def set_snapshot(self, snapshot: dict) -> None:
        backend = snapshot.get("backend") or {}
        pairing = snapshot.get("pairing") or {}
        connection = snapshot.get("connection") or {}
        phase = snapshot.get("phase", "")
        connection_state = _humanize_state(
            str(
                backend.get("state")
                or connection.get("state")
                or phase
                or "disconnected"
            )
        )
        if connection.get("state") == "blocked" and phase in {"connecting", "stopping"}:
            connection_state = "connecting"
        elif str(backend.get("recovery_state", "")).lower() == "recovering":
            connection_state = "recovering"
        elif phase in {"connecting", "stopping"}:
            connection_state = "connecting"
        elif backend.get("state") == "running" or connection.get("state") == "ready":
            connection_state = "connected"
        self.connection_state.setText(connection_state)
        self.transport.setText(_humanize_transport(str(backend.get("transport_selected", "") or "n/a")))
        self.failover.setText(str(backend.get("failover_count", "0") or "0"))
        recovery = str(backend.get("recovery_state", "") or "n/a")
        if backend.get("transport_previous"):
            recovery += f" (previous: {_humanize_transport(str(backend.get('transport_previous', '')))} )"
        self.recovery.setText(recovery.replace(" )", ")"))
        self.session_id.setText(str(backend.get("carrier_session_id", "") or "n/a"))
        latency = str(backend.get("carrier_latency_ms", "") or "")
        self.latency.setText(f"{latency} ms" if latency and latency not in {"0", "0.0"} else ("0 ms" if latency == "0" else "n/a"))
        self.credential_expiry.setText(_format_countdown(pairing.get("credential_expires_at")))


class GatePanel(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("gate-panel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        self.title = QLabel("Connection gate")
        self.title.setObjectName("banner-title")
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.next_step = QLabel()
        self.next_step.setWordWrap(True)
        self.next_step.setObjectName("summary")
        self.action = QPushButton("Retry connect")
        self.action.setObjectName("primary")
        layout.addWidget(self.title)
        layout.addWidget(self.body)
        layout.addWidget(self.next_step)
        layout.addWidget(self.action, 0, Qt.AlignmentFlag.AlignLeft)
        self.hide()

    def set_gate(self, gate: dict) -> None:
        state = str(gate.get("state", "ready"))
        if state != "blocked":
            self.hide()
            return
        self.title.setText(str(gate.get("title") or "Connection blocked"))
        self.body.setText(str(gate.get("message") or "Connection is not ready yet."))
        self.next_step.setText(str(gate.get("next_step") or ""))
        self.action.setText(_gate_action_text(str(gate.get("code", ""))))
        self.show()


class AccentPanel(QFrame):
    def __init__(self, title: str, body: str, *, object_name: str = "accent-panel") -> None:
        super().__init__()
        self.setObjectName(object_name)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)
        title_label = QLabel(title)
        title_label.setObjectName("accent-title")
        body_label = QLabel(body)
        body_label.setObjectName("accent-body")
        body_label.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(body_label)


class StatusBanner(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("banner-info")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)
        self.title = QLabel()
        self.title.setObjectName("banner-title")
        self.body = QLabel()
        self.body.setObjectName("banner-body")
        self.body.setWordWrap(True)
        layout.addWidget(self.title)
        layout.addWidget(self.body)
        self.set_banner(Banner("Welcome", "Start with your Bale phone number.", "info"))

    def set_banner(self, banner: Banner) -> None:
        self.title.setText(banner.title)
        self.body.setText(banner.body)
        self.setObjectName(f"banner-{banner.tone}")
        self.style().unpolish(self)
        self.style().polish(self)


class TimelineWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._labels: list[QLabel] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        for _ in range(4):
            label = QLabel()
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setMinimumHeight(36)
            self._labels.append(label)
            layout.addWidget(label)

    def set_steps(self, steps) -> None:  # noqa: ANN001
        for label, step in zip(self._labels, steps):
            state = "step-done" if step.done else "step-current" if step.current else "step-pending"
            label.setObjectName(state)
            label.setText(step.label)
            label.style().unpolish(label)
            label.style().polish(label)


class ReadinessWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._labels: list[QLabel] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        for _ in range(4):
            label = QLabel()
            label.setWordWrap(True)
            self._labels.append(label)
            layout.addWidget(label)

    def set_items(self, items) -> None:  # noqa: ANN001
        for label, item in zip(self._labels, items):
            icon = "Ready" if item.ready else "Needs setup"
            label.setText(f"{item.label}: {icon}. {item.detail}")
            label.setObjectName("readiness-ok" if item.ready else "readiness-pending")
            label.style().unpolish(label)
            label.style().polish(label)


class LoginView(QWidget):
    def __init__(self, parent: "MainWindow") -> None:
        super().__init__(parent)
        self._main = parent
        self._sms_worker: Optional[StartSmsWorker] = None
        self._validate_worker: Optional[ValidateCodeWorker] = None
        self._auth_handle = None
        self._stage = "phone"
        self.setAutoFillBackground(True)
        self.setStyleSheet("background-color: #f4f8f7; color: #10211f;")

        card = QWidget(objectName="card")
        card.setMaximumWidth(520)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(32, 30, 32, 30)
        card_layout.setSpacing(14)

        title = QLabel("Welcome to baleobala")
        title.setObjectName("title")
        subtitle = QLabel("Use your Bale phone number once, then the app will guide you through pairing and connection.")
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)

        self.stage_label = QLabel()
        self.stage_label.setObjectName("eyebrow")
        intro = AccentPanel(
            "The shortest path",
            "Sign in once, verify the SMS code, then move directly into relay setup. You do not need to configure transport settings here.",
            object_name="hero-panel",
        )
        self.banner = StatusBanner()

        self.phone = QLineEdit()
        self.phone.setPlaceholderText("+98 912 345 6789")
        self.phone.setClearButtonEnabled(True)
        self.phone.textChanged.connect(self._refresh_actions)

        self.send_btn = QPushButton("Send SMS code")
        self.send_btn.setObjectName("primary")
        self.send_btn.clicked.connect(self._on_send_clicked)

        self.code = QLineEdit()
        self.code.setPlaceholderText("12345")
        self.code.setEnabled(False)
        self.code.setMaxLength(8)
        self.code.textChanged.connect(self._refresh_actions)

        self.verify_btn = QPushButton("Verify and continue")
        self.verify_btn.setObjectName("primary")
        self.verify_btn.clicked.connect(self._on_verify_clicked)

        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        form.addRow("Phone number", self.phone)
        form.addRow("SMS code", self.code)

        button_row = QHBoxLayout()
        button_row.addWidget(self.send_btn)
        button_row.addWidget(self.verify_btn)

        card_layout.addWidget(self.stage_label)
        card_layout.addWidget(title)
        card_layout.addWidget(subtitle)
        card_layout.addWidget(intro)
        card_layout.addWidget(self.banner)
        card_layout.addSpacing(6)
        card_layout.addLayout(form)
        card_layout.addLayout(button_row)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(24, 36, 24, 36)
        outer.addStretch(1)
        outer.addWidget(card)
        outer.addStretch(1)

        self._set_stage("phone")
        self._refresh_actions()

    def _set_stage(self, stage: str) -> None:
        self._stage = stage
        labels = {
            "phone": "Step 1 of 3  Sign in",
            "code": "Step 2 of 3  Enter code",
            "done": "Step 3 of 3  Signed in",
        }
        self.stage_label.setText(labels.get(stage, labels["phone"]))

    def _refresh_actions(self) -> None:
        phone_ok, _ = validate_phone_input(self.phone.text())
        code_ok, _ = validate_sms_code(self.code.text())
        self.send_btn.setEnabled(phone_ok and self._stage != "done")
        self.verify_btn.setEnabled(self.code.isEnabled() and code_ok and self._auth_handle is not None)

    def _set_banner(self, title: str, body: str, tone: str = "info") -> None:
        self.banner.set_banner(Banner(title, body, tone))

    def _on_send_clicked(self) -> None:
        phone_ok, message = validate_phone_input(self.phone.text())
        if not phone_ok:
            self._set_banner("Phone number needs attention", message, "err")
            return
        self.send_btn.setEnabled(False)
        self._set_stage("phone")
        self._set_banner("Sending code", "We are requesting an SMS code from Bale. Keep this window open.", "info")
        worker = StartSmsWorker(self.phone.text().strip())
        worker.ok.connect(self._on_sms_sent)
        worker.failed.connect(self._on_sms_failed)
        self._sms_worker = worker
        run_in_thread(worker)

    def _on_sms_sent(self, _tx: str) -> None:
        self._auth_handle = self._sms_worker.auth_handle() if self._sms_worker else None
        self.code.setEnabled(True)
        self.code.setFocus()
        self.send_btn.setText("Resend code")
        self._set_stage("code")
        self._set_banner("Code sent", "Enter the SMS code below, then continue to the pairing screen.", "ok")
        self._refresh_actions()

    def _on_sms_failed(self, err: str) -> None:
        self.send_btn.setEnabled(True)
        self._set_banner("We could not send the SMS code", friendly_error_message(err, context="login"), "err")
        self._refresh_actions()

    def _on_verify_clicked(self) -> None:
        code_ok, message = validate_sms_code(self.code.text())
        if not code_ok:
            self._set_banner("Code needs attention", message, "err")
            return
        if self._auth_handle is None:
            self._set_banner("Send a code first", "Start by requesting an SMS code, then enter it here.", "err")
            return
        self.verify_btn.setEnabled(False)
        self._set_banner("Verifying code", "If the code is valid, we will move you straight into relay setup.", "info")
        worker = ValidateCodeWorker(self._auth_handle, self.code.text().strip())
        worker.ok.connect(self._on_verified)
        worker.failed.connect(self._on_verify_failed)
        self._validate_worker = worker
        run_in_thread(worker)

    def _on_verified(self, jwt: str) -> None:
        phone = self.phone.text().strip().lstrip("+")
        try:
            self._main.service.save_auth_jwt(jwt, phone=phone)
        except Exception as e:
            log.warning("keychain save failed: %s", e)
        self._set_stage("done")
        self._set_banner("Signed in", "Your Bale account is ready. Next, create or reuse a relay pairing.", "ok")
        self._main.set_jwt(jwt, phone=phone)
        self._main.show_connect_view()

    def _on_verify_failed(self, err: str) -> None:
        self.verify_btn.setEnabled(True)
        self._set_banner("Code verification failed", friendly_error_message(err, context="login"), "err")
        self._refresh_actions()


class ConnectView(QWidget):
    def __init__(self, parent: "MainWindow") -> None:
        super().__init__(parent)
        self._main = parent
        self._worker = None
        self._worker_thread: Optional[QThread] = None
        self._phase = "idle"
        self._logs_open = False
        self.setAutoFillBackground(True)
        self.setStyleSheet("background-color: #f4f8f7; color: #10211f;")

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        title = QLabel("Secure Connection")
        title.setObjectName("title")
        subtitle = QLabel("Follow the guided path below. Advanced transport settings stay tucked away unless you need them.")
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)

        self.banner = StatusBanner()
        self.timeline = TimelineWidget()
        self.readiness = ReadinessWidget()
        self.account_summary = QLabel()
        self.account_summary.setObjectName("summary")
        self.dashboard = ConnectionDashboard()
        self.gate_panel = GatePanel()
        self.gate_panel.action.clicked.connect(self._on_gate_action_clicked)
        self._latest_snapshot: dict = {}
        self._manual_detail = ""

        root.addWidget(title)
        root.addWidget(subtitle)
        root.addWidget(self.banner)
        root.addWidget(self.timeline)
        root.addWidget(_section("Readiness"))
        root.addWidget(self.readiness)
        root.addWidget(self.account_summary)
        root.addWidget(_section("Connection Status"))
        root.addWidget(self.dashboard)
        root.addWidget(self.gate_panel)

        callouts = QHBoxLayout()
        callouts.setSpacing(10)
        callouts.addWidget(
            AccentPanel(
                "Recommended path",
                "Keep advanced settings on their defaults unless you are debugging a specific backend or port.",
            )
        )
        callouts.addWidget(
            AccentPanel(
                "What happens next",
                "Starting the secure connection checks the saved profile, relay pairing, and runtime, then opens the active transport.",
            )
        )

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("hero-action")
        self.connect_btn.setCheckable(True)
        self.connect_btn.setMinimumHeight(58)
        self.connect_btn.setMinimumWidth(300)
        self.connect_btn.clicked.connect(self._on_primary_clicked)

        quick_row = QHBoxLayout()
        quick_row.addStretch(1)
        quick_row.addWidget(self.connect_btn)
        quick_row.addStretch(1)

        self.setup_toggle = QToolButton()
        self.setup_toggle.setText("Show setup options")
        self.setup_toggle.setCheckable(True)
        self.setup_toggle.toggled.connect(self._toggle_setup)

        self.setup_panel = QFrame()
        self.setup_panel.setObjectName("settings-panel")
        self.setup_panel_layout = QVBoxLayout(self.setup_panel)
        self.setup_panel_layout.setContentsMargins(0, 0, 0, 0)
        self.setup_panel_layout.setSpacing(12)
        self.advanced_section = _section("Advanced")

        self.quick_start = QGroupBox("Quick Start")
        self.quick_start.setObjectName("quick-start")
        quick_layout = QFormLayout(self.quick_start)
        quick_layout.setHorizontalSpacing(14)
        quick_layout.setVerticalSpacing(10)

        self.role = QComboBox()
        self.role.addItem("This device uses relay", "client")
        self.role.addItem("This device shares connection", "relay")
        self.role.currentIndexChanged.connect(self._refresh_enabled)

        self.pair_name = QLineEdit()
        self.pair_name.setPlaceholderText("home-relay")

        self.pair_code = QLineEdit()
        self.pair_code.setPlaceholderText("Optional existing pairing code")

        quick_layout.addRow("Device role", self.role)
        quick_layout.addRow("Relay name", self.pair_name)
        quick_layout.addRow("Pairing code", self.pair_code)

        self.state_label = QLabel()
        self.state_label.setObjectName("status-info")
        self.state_label.setWordWrap(True)
        self.phase_detail = self.state_label
        self.state_label.setVisible(False)

        self.sharing = QGroupBox("Invite / Join")
        self.sharing.setObjectName("invite-join")
        sharing_layout = QVBoxLayout(self.sharing)
        sharing_layout.setSpacing(10)

        self.invite_btn = QPushButton("Invite")
        self.invite_btn.clicked.connect(self._on_invite_clicked)

        self.invite_link = QLineEdit()
        self.invite_link.setReadOnly(True)
        self.invite_link.setPlaceholderText("Invite link will appear here")
        self.invite_link.setClearButtonEnabled(True)

        self.invite_qr = QLabel("Invite QR will appear here")
        self.invite_qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.invite_qr.setMinimumSize(240, 240)
        self.invite_qr.setObjectName("invite-qr")
        self.invite_qr.setWordWrap(True)

        self.join_link = QLineEdit()
        self.join_link.setPlaceholderText("Paste baleobala://pair?... invite link")
        self.join_link.setClearButtonEnabled(True)

        self.join_btn = QPushButton("Join Invite")
        self.join_btn.clicked.connect(self._on_join_clicked)

        sharing_top = QHBoxLayout()
        sharing_top.addWidget(self.invite_btn)
        sharing_top.addWidget(self.join_btn)
        sharing_top.addStretch(1)

        sharing_layout.addLayout(sharing_top)
        sharing_layout.addWidget(self.invite_link)
        sharing_layout.addWidget(self.invite_qr)
        sharing_layout.addWidget(self.join_link)

        self.advanced = QGroupBox("Advanced Settings")
        self.advanced.setObjectName("advanced-settings")
        advanced_layout = QFormLayout(self.advanced)
        advanced_layout.setHorizontalSpacing(14)
        advanced_layout.setVerticalSpacing(10)

        self.backend = QComboBox()
        self.backend.addItem("Use saved default", "")
        self.backend.addItem("Packet tunnel", "packet-tunnel")
        try:
            from baleobala.control.paths import is_android_runtime

            if is_android_runtime():
                self.backend.addItem("Android VPN", "android-vpn")
        except Exception:  # noqa: BLE001
            pass
        self.backend.addItem("Linux TUN", "linux-tun")
        self.backend.addItem("Proxy fallback", "proxy")
        self.backend.addItem("Direct proxy", "direct")

        self.proxy_secret = QLineEdit()
        self.proxy_secret.setPlaceholderText("Optional shared secret")
        self.proxy_secret.setEchoMode(QLineEdit.EchoMode.Password)

        self.listen_port = QSpinBox()
        self.listen_port.setRange(1024, 65535)
        self.listen_port.setValue(1080)

        advanced_layout.addRow("Connection backend", self.backend)
        advanced_layout.addRow("Shared secret", self.proxy_secret)
        advanced_layout.addRow("Local SOCKS5 port", self.listen_port)

        self.setup_panel_layout.addWidget(self.quick_start)
        self.setup_panel_layout.addWidget(self.sharing)
        self.setup_panel_layout.addWidget(self.advanced_section)
        self.setup_panel_layout.addWidget(self.advanced)

        self.logs_toggle = QToolButton()
        self.logs_toggle.setText("Show Connection Log")
        self.logs_toggle.setCheckable(True)
        self.logs_toggle.toggled.connect(self._toggle_logs)

        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(1500)
        self.logs.setPlaceholderText("Connection details will appear here when needed.")
        self.logs.setVisible(False)

        root.addLayout(quick_row)
        root.addLayout(callouts)
        root.addWidget(self.setup_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        root.addWidget(self.setup_panel)
        root.addWidget(self.state_label)
        self.setup_panel.setVisible(False)
        root.addWidget(self.logs_toggle)
        root.addWidget(self.logs, 1)

        self._refresh_enabled()
        self._refresh_status()

    def _toggle_setup(self, open_: bool) -> None:
        self.setup_toggle.setText("Hide setup options" if open_ else "Show setup options")
        self.setup_panel.setVisible(open_)

    def _toggle_logs(self, open_: bool) -> None:
        self._logs_open = open_
        self.logs_toggle.setText("Hide Connection Log" if open_ else "Show Connection Log")
        self.logs.setVisible(open_)

    def _refresh_enabled(self) -> None:
        is_client = self.role.currentData() == "client"
        editable = self._phase not in {"connecting", "connected", "stopping"}
        self.listen_port.setEnabled(is_client and editable)
        for widget in (self.role, self.pair_name, self.pair_code, self.backend, self.proxy_secret):
            widget.setEnabled(editable)
        for widget in (self.invite_btn, self.join_btn, self.join_link):
            widget.setEnabled(editable)
        self.connect_btn.setText("Disconnect" if self._phase in {"connecting", "connected"} else "Connect")
        self.connect_btn.setChecked(self._phase in {"connecting", "connected"})
        self.connect_btn.style().unpolish(self.connect_btn)
        self.connect_btn.style().polish(self.connect_btn)

    def _refresh_status(self, detail: str = "") -> None:
        if detail:
            self._manual_detail = detail
        snapshot_obj = self._latest_snapshot or self._main.service.status()
        if hasattr(snapshot_obj, "__dataclass_fields__"):
            from dataclasses import asdict

            snapshot = asdict(snapshot_obj)
        elif isinstance(snapshot_obj, dict):
            snapshot = dict(snapshot_obj)
        elif hasattr(snapshot_obj, "__dict__"):
            snapshot = dict(snapshot_obj.__dict__)
        else:
            snapshot = {"auth": {}, "pairing": {}, "backend": {}, "vpn": {}, "connection": {}}
        self._latest_snapshot = snapshot
        connection = snapshot.get("connection", {})
        signed_in = snapshot.get("auth", {}).get("state") == "configured"
        auth_state = snapshot.get("auth", {}).get("state", "empty")
        pairing = snapshot.get("pairing", {})
        pairing_state = pairing.get("state", "empty")
        pairing_authorization = pairing.get("authorization_status", "")
        pairing_provisioning = pairing.get("provisioning_status", "")
        pairing_revoked = pairing.get("revoked_at", "") not in {"", "none"} or pairing_authorization == "revoked" or pairing_provisioning == "revoked"
        pairing_rejected = pairing_authorization == "rejected" or pairing_provisioning == "rejected"
        pairing_ready = pairing_state in {"paired", "accepted", "complete"} and pairing_provisioning in {"complete", "accepted"} and not pairing_revoked and not pairing_rejected
        backend = snapshot.get("backend", {})
        backend_name = backend.get("backend", "")
        direct_ready = backend_name == "direct" and connection.get("state") == "ready"
        paired = pairing_ready or direct_ready
        saved_profile = snapshot.get("vpn", {}).get("state") == "configured"
        phase = "authenticated" if signed_in and not paired and self._phase == "idle" else self._phase
        banner = connect_banner(
            signed_in=signed_in,
            paired=paired,
            phase=phase,
            detail=self._manual_detail or connection.get("message", ""),
            auth_state=auth_state,
            connection=connection,
        )
        self.banner.set_banner(banner)
        self.timeline.set_steps(connect_timeline(signed_in=signed_in, paired=paired, phase=phase))
        self.readiness.set_items(
            connect_readiness(
                signed_in=signed_in,
                paired=paired,
                saved_profile=saved_profile,
                phase=phase,
                auth_state=auth_state,
                pairing_state=pairing_state,
                connection=connection,
            )
        )
        account_text = "Signed in account: " + (("+" + self._main.phone) if self._main.phone else "saved Bale account") if signed_in else "Signed in account: none"
        if pairing_ready:
            pairing_text = f"Relay pairing: {pairing.get('name', 'saved relay')}"
        elif pairing_revoked:
            pairing_text = f"Relay pairing: revoked ({pairing.get('name', 'saved relay')})"
        elif pairing_rejected:
            pairing_text = f"Relay pairing: rejected ({pairing.get('name', 'saved relay')})"
        elif connection.get("backend") == "direct" and connection.get("state") == "ready":
            pairing_text = "Relay pairing: not required for direct proxy debug path."
        else:
            pairing_text = "Relay pairing: not ready yet"
        self.account_summary.setText(f"{account_text}  |  {pairing_text}")
        self.dashboard.set_snapshot(
            {
                "backend": backend,
                "pairing": pairing,
                "connection": connection,
                "phase": phase,
                "auth": snapshot.get("auth", {}),
            }
        )
        self.gate_panel.set_gate(connection)
        self.state_label.setText(detail or self._manual_detail or banner.body)
        busy = self._phase in {"connecting", "connected", "stopping"}
        self.connect_btn.setEnabled(
            (self._phase in {"connecting", "connected"}) or (not busy and connection.get("state") != "blocked" and signed_in and paired)
        )

    def _set_phase(self, phase: str, detail: str = "") -> None:
        self._phase = phase
        self._refresh_enabled()
        self._refresh_status(detail)

    def _append_log(self, line: str) -> None:
        safe_line = line.replace(self.proxy_secret.text().strip(), "<redacted>") if self.proxy_secret.text().strip() else line
        self.logs.appendPlainText(safe_line)

    def _refresh_invite_preview(self, link: str) -> None:
        self.invite_link.setText(link)
        pixmap = _build_qr_pixmap(link)
        if pixmap is None:
            self.invite_qr.setPixmap(QPixmap())
            self.invite_qr.setText("QR preview unavailable. Install qrcode to render the invite as an image.")
            return
        self.invite_qr.setPixmap(pixmap)
        self.invite_qr.setText("")

    def _on_primary_clicked(self) -> None:
        if self._phase in {"connecting", "connected"}:
            self._stop_session()
        else:
            self._start_session()

    def _on_gate_action_clicked(self) -> None:
        snapshot = self._latest_snapshot or self._main.service.status()
        if hasattr(snapshot, "__dataclass_fields__"):
            from dataclasses import asdict

            snapshot = asdict(snapshot)
        connection = snapshot.get("connection", {})
        pairing = snapshot.get("pairing", {})
        code = str(connection.get("code", ""))
        profile_id = str(pairing.get("profile_id", "") or "")
        if code in {"auth_missing", "auth_expired"}:
            self._main.show_login_view()
            return
        if code == "pairing_missing":
            pair_name = self.pair_name.text().strip() or "home-relay"
            role = self.role.currentData()
            backend = self.backend.currentData() or "packet-tunnel"
            created = self._ensure_pairing(pair_name=pair_name, pair_code="", role=role)
            self._persist_profile_defaults(pair_name=pair_name, role=role, backend=backend)
            self.setup_toggle.setChecked(True)
            if created is not None:
                self._set_phase("authenticated", f"Created pairing {created.profile_id}.")
            else:
                self._set_phase("authenticated", "Pairing setup is ready.")
            return
        if code == "credentials_expired" and profile_id:
            self._main.service.refresh_pairing_credentials(profile_id)
            self._set_phase("authenticated", "Credential bundle refreshed.")
            return
        if code in {"credentials_missing", "peer_assignment_missing"} and profile_id:
            self._main.service.sync_pairing(profile_id)
            self._set_phase("authenticated", "Pairing synchronized.")
            return
        if code in {"access_revoked", "access_rejected", "provisioning_pending", "provisioning_missing"}:
            self.setup_toggle.setChecked(True)
            self._set_phase("authenticated", "Pairing needs attention before the connection can start.")
            return
        self._start_session()

    def _on_invite_clicked(self) -> None:
        issues = self._preflight()
        if issues:
            self._set_phase("error", " ".join(issues))
            return
        pair_name = self.pair_name.text().strip() or "home-relay"
        role = self.role.currentData()
        if self.backend.currentData() != "direct":
            self._ensure_pairing(pair_name=pair_name, pair_code=self.pair_code.text().strip(), role=role)
        else:
            self._persist_profile_defaults(pair_name=pair_name or "direct-proxy", role=role, backend="direct")
        pairing = self._main.service.load_pairing()
        if pairing is None:
            self._set_phase("error", "Create or join a pairing before generating an invite.")
            return
        try:
            link = self._main.service.export_pairing_invite(pairing.profile_id)
        except Exception as exc:
            self._set_phase("error", friendly_error_message(str(exc), context="connect"))
            return
        self._refresh_invite_preview(link)
        self._set_phase("authenticated", "Invite ready. Share the link or let the other device scan the QR code.")

    def _on_join_clicked(self) -> None:
        link = self.join_link.text().strip()
        if not link:
            self._set_phase("error", "Paste a baleobala://pair invite link first.")
            return
        try:
            record = self._main.service.import_pairing_invite(link)
        except Exception as exc:
            self._set_phase("error", friendly_error_message(str(exc), context="connect"))
            return
        self.pair_name.setText(record.name)
        if record.role == "relay":
            self.role.setCurrentIndex(1)
        else:
            self.role.setCurrentIndex(0)
        self.pair_code.setText("")
        self._persist_profile_defaults(pair_name=record.name, role=record.role, backend=self.backend.currentData() or record.backend_preference)
        self._set_phase("authenticated", f"Joined invite for {record.name}. You can start the secure connection now.")

    def _preflight(self) -> list[str]:
        issues: list[str] = []
        if not self._main.jwt:
            issues.append("Sign in with your Bale phone number first.")
        if self.backend.currentData() != "direct" and not self.pair_name.text().strip() and not self._main.service.load_pairing() and not self.pair_code.text().strip():
            issues.append("Enter a relay name or provide an existing pairing code.")
        return issues

    def _ensure_pairing(self, *, pair_name: str, pair_code: str, role: str):
        if self.backend.currentData() == "direct" and not pair_code and not pair_name and self._main.service.load_pairing() is None:
            return None
        pairing = self._main.service.load_pairing()
        if pair_code:
            pairing = self._main.service.accept_pairing(pair_code, name=pair_name)
            self._append_log(f"accepted pairing {pairing.profile_id}")
        elif pairing is None:
            pairing = self._main.service.begin_pairing(
                pair_name,
                role=role,
                relay_mode="proxy",
                backend_preference=self.backend.currentData() or "",
            )
            self._append_log(f"created pairing {pairing.profile_id}")
        return pairing

    def _persist_profile_defaults(self, *, pair_name: str, role: str, backend: str) -> None:
        profile = self._main.service.ensure_profile()
        pairing = self._main.service.load_pairing()
        self._main.service.save_profile(
            type(profile)(
                profile_id=profile.profile_id,
                name=pair_name,
                backend=backend or profile.backend,
                role=role,
                pairing_id=pairing.profile_id if pairing is not None else profile.pairing_id,
                peer_id=pairing.peer_id if pairing is not None else profile.peer_id,
                peer_name=pairing.peer_name if pairing is not None else profile.peer_name,
                answer=role == "relay",
                auto_start=profile.auto_start,
                listen_host=profile.listen_host,
                listen_port=self.listen_port.value(),
                protocol=profile.protocol,
                volume=profile.volume,
                proxy_secret=self.proxy_secret.text().strip() or profile.proxy_secret,
            )
        )

    def _start_session(self) -> None:
        issues = self._preflight()
        if issues:
            self._set_phase("error", " ".join(issues))
            return
        pair_name = self.pair_name.text().strip()
        pair_code = self.pair_code.text().strip()
        role = self.role.currentData()
        backend = self.backend.currentData()
        if backend != "direct":
            pair_name = pair_name or "home-relay"
            self._ensure_pairing(pair_name=pair_name, pair_code=pair_code, role=role)
        else:
            pair_name = pair_name or "direct-proxy"
        self._persist_profile_defaults(pair_name=pair_name, role=role, backend=backend)
        self.logs.clear()
        worker = ControlPlaneConnectWorker(self._main.service)
        worker.connecting.connect(self._on_connecting)
        worker.connected.connect(self._on_connected)
        worker.stopped.connect(self._on_stopped)
        worker.failed.connect(self._on_failed)
        worker.log_line.connect(self._append_log)
        self._worker = worker
        self._worker_thread = run_in_thread(worker)
        self._set_phase("connecting", "Checking your saved profile, relay pairing, and runtime.")

    def _stop_session(self) -> None:
        worker = ControlPlaneConnectWorker(self._main.service, disconnect=True)
        worker.connecting.connect(self._on_connecting)
        worker.stopped.connect(self._on_stopped)
        worker.failed.connect(self._on_failed)
        worker.log_line.connect(self._append_log)
        self._worker = worker
        self._worker_thread = run_in_thread(worker)
        self.connect_btn.setEnabled(False)
        self._set_phase("stopping", "Closing the active secure connection.")

    def _on_connecting(self, msg: str) -> None:
        self._set_phase("connecting", msg)
        self._append_log(msg)

    def _on_connected(self, payload: dict) -> None:
        backend = payload.get("backend", {})
        probe = payload.get("probe", {})
        transport = backend.get("transport_selected", "saved transport")
        recovery = backend.get("recovery_state", "")
        failover_count = backend.get("failover_count", "0")
        previous = backend.get("transport_previous", "")
        coordination = backend.get("peer_coordination", "")
        if recovery == "recovering":
            detail = (
                f"Connection is recovering via {backend.get('backend', 'backend')}. "
                f"Current transport target is {transport}."
            )
        elif recovery == "failed":
            detail = (
                f"Connection degraded on {backend.get('backend', 'backend')}. "
                f"Last transport was {transport}."
            )
        else:
            detail = f"Connection is active via {backend.get('backend', 'backend')} using {transport}."
        if previous:
            detail += f" Previous transport: {previous}."
        if failover_count not in {"", "0"}:
            detail += f" Failovers so far: {failover_count}."
        if coordination == "active":
            detail += " Peer failover coordination is active."
        if probe.get("ok") == "yes" and probe.get("detail"):
            detail += f" {probe.get('detail')}"
        self._set_phase("connected", detail)
        self._append_log(json.dumps({"backend": backend, "probe": probe}, sort_keys=True))

    def _on_stopped(self, payload: dict) -> None:
        if payload:
            self._append_log(json.dumps(payload, sort_keys=True))
        self._worker = None
        self._worker_thread = None
        self._set_phase("idle", "Connection stopped. You can start it again whenever you are ready.")

    def _on_failed(self, err: str) -> None:
        self._worker = None
        self._worker_thread = None
        self._set_phase("error", friendly_error_message(err, context="connect"))
        self._append_log("FAIL " + err)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("baleobala")
        self.resize(960, 820)
        self.setMinimumSize(860, 680)
        self.setAutoFillBackground(True)
        self.setStyleSheet("QMainWindow { background: #f4f8f7; color: #10211f; }")

        self._service = ControlService()
        self.jwt: Optional[str] = None
        self.phone: Optional[str] = None
        self._status_worker = None
        self._status_thread: Optional[QThread] = None
        self._status_snapshot: dict = {}

        content = QWidget(objectName="container")
        content.setAutoFillBackground(True)
        content.setStyleSheet("background-color: #f4f8f7;")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        self.login_view = LoginView(self)
        self.connect_view = ConnectView(self)
        self.login_view.setStyleSheet(self.login_view.styleSheet() + " QWidget { background-color: #f4f8f7; color: #10211f; }")
        self.connect_view.setStyleSheet(self.connect_view.styleSheet() + " QWidget { background-color: #f4f8f7; color: #10211f; }")
        layout.addWidget(self.login_view)
        layout.addWidget(self.connect_view)
        self.setCentralWidget(content)

        status = QStatusBar()
        self.setStatusBar(status)
        self._status_label = QLabel()
        status.addWidget(self._status_label, 1)

        self._logout_action = QAction("Sign out", self)
        self._logout_action.triggered.connect(self._on_logout)
        self.menuBar().addAction(self._logout_action)

        try:
            record = self._service.load_auth()
        except Exception as e:
            log.warning("auth load failed: %s", e)
            record = None
        if record and record.jwt:
            self.set_jwt(record.jwt, phone=record.phone)
            self.show_connect_view()
        else:
            self.show_login_view()
        self._update_status()
        self._start_status_polling()

        handler = QtLogHandler(level=logging.INFO)
        handler.line.connect(self.connect_view._append_log)
        logging.getLogger("baleobala").addHandler(handler)

    @property
    def service(self) -> ControlService:
        return self._service

    def set_jwt(self, jwt: str, *, phone: Optional[str] = None) -> None:
        self.jwt = jwt
        self.phone = phone
        self._update_status()
        if self._status_snapshot:
            self.connect_view._latest_snapshot = self._status_snapshot
        self.connect_view._refresh_status()

    def show_connect_view(self) -> None:
        self.login_view.setVisible(False)
        self.connect_view.setVisible(True)
        self._logout_action.setVisible(True)
        if self._status_snapshot:
            self.connect_view._latest_snapshot = self._status_snapshot
        self.connect_view._refresh_status()

    def show_login_view(self) -> None:
        self.connect_view.setVisible(False)
        self.login_view.setVisible(True)
        self._logout_action.setVisible(False)

    def _update_status(self) -> None:
        if self.jwt:
            who = ("+" + self.phone) if self.phone else "saved account"
            self._status_label.setText(f"Signed in as {who}")
        else:
            self._status_label.setText("Not signed in")

    def _on_logout(self) -> None:
        try:
            self._service.clear_auth()
        except Exception as e:
            log.warning("clear failed: %s", e)
        self.jwt = None
        self.phone = None
        self._update_status()
        self.show_login_view()
        self.connect_view._manual_detail = ""
        if self._status_snapshot:
            self.connect_view._latest_snapshot = self._status_snapshot
        self.connect_view._set_phase("idle", "Sign in again to create or reuse a relay pairing.")

    def _start_status_polling(self) -> None:
        if self._status_worker is not None:
            return
        worker = StatusPoller(self._service, interval_seconds=2.0)
        worker.snapshot.connect(self._on_status_snapshot)
        worker.failed.connect(lambda msg: log.warning("status poll failed: %s", msg))
        self._status_worker = worker
        self._status_thread = run_in_thread(worker)

    def _on_status_snapshot(self, snapshot: dict) -> None:
        self._status_snapshot = snapshot
        if self.connect_view.isVisible():
            self.connect_view._latest_snapshot = snapshot
            self.connect_view._refresh_status()

    def closeEvent(self, event) -> None:  # noqa: ANN001
        worker = self._status_worker
        if worker is not None:
            try:
                worker.stop()
            except Exception:
                pass
        thread = self._status_thread
        if thread is not None:
            thread.quit()
            thread.wait(2500)
        self._status_worker = None
        self._status_thread = None
        super().closeEvent(event)


def run(argv: Optional[list[str]] = None) -> int:
    _configure_gui_logging()
    app = QApplication(argv or sys.argv)
    app.setApplicationName("baleobala")
    apply_theme(app, mode="light")
    win = MainWindow()
    win.show()
    return app.exec()
