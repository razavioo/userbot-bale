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
from baleobala.control.proxy_config import (
    load_proxy_config,
    save_proxy_config,
)
from baleobala.gui.theme import apply_theme
from baleobala.gui.workers import (
    DirectProxyWorker,
    QtLogHandler,
    StartSmsWorker,
    ValidateCodeWorker,
    run_in_thread,
)
from baleobala.presentation import (
    Banner,
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
    """Simple connect/disconnect view. Dials the configured relay peer_id directly."""

    def __init__(self, parent: "MainWindow") -> None:
        super().__init__(parent)
        self._main = parent
        self._worker: Optional[DirectProxyWorker] = None
        self._worker_thread: Optional[QThread] = None
        self._phase = "idle"   # idle | connecting | connected | stopping
        self._logs_open = False
        self.setAutoFillBackground(True)
        self.setStyleSheet("background-color: #f4f8f7; color: #10211f;")

        # Load saved proxy config
        _cfg = load_proxy_config()

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 28)
        root.setSpacing(18)

        # ── Title ────────────────────────────────────────────────────
        title = QLabel("Secure Proxy")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        phone_text = ("+" + parent.phone) if parent.phone else "your Bale account"
        self._account_label = QLabel(f"Signed in as {phone_text}")
        self._account_label.setObjectName("summary")
        self._account_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ── Status card ──────────────────────────────────────────────
        self._status_card = QFrame()
        self._status_card.setObjectName("dashboard-panel")
        status_layout = QVBoxLayout(self._status_card)
        status_layout.setContentsMargins(20, 16, 20, 16)
        status_layout.setSpacing(6)

        self._status_dot = QLabel("●")
        self._status_dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_dot.setStyleSheet("font-size: 32px; color: #6b7c77;")

        self._status_text = QLabel("Disconnected")
        self._status_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_text.setObjectName("banner-title")

        self._proxy_addr = QLabel("")
        self._proxy_addr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._proxy_addr.setObjectName("summary")
        self._proxy_addr.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._proxy_addr.setVisible(False)

        status_layout.addWidget(self._status_dot)
        status_layout.addWidget(self._status_text)
        status_layout.addWidget(self._proxy_addr)

        # ── Big connect button ────────────────────────────────────────
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("hero-action")
        self.connect_btn.setCheckable(True)
        self.connect_btn.setMinimumHeight(64)
        self.connect_btn.setMinimumWidth(260)
        self.connect_btn.clicked.connect(self._on_primary_clicked)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(self.connect_btn)
        btn_row.addStretch(1)

        # ── Settings panel (collapsed by default) ────────────────────
        self._settings_toggle = QToolButton()
        self._settings_toggle.setText("⚙  Settings")
        self._settings_toggle.setCheckable(True)
        self._settings_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._settings_toggle.toggled.connect(self._toggle_settings)

        self._settings_panel = QFrame()
        self._settings_panel.setObjectName("settings-panel")
        settings_form = QFormLayout(self._settings_panel)
        settings_form.setContentsMargins(16, 14, 16, 14)
        settings_form.setHorizontalSpacing(14)
        settings_form.setVerticalSpacing(10)

        self.listen_port = QSpinBox()
        self.listen_port.setRange(1024, 65535)
        self.listen_port.setValue(int(_cfg.get("listen_port") or 10800))

        self.proxy_secret = QLineEdit()
        self.proxy_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.proxy_secret.setPlaceholderText("Relay shared secret")
        self.proxy_secret.setText(_cfg.get("proxy_secret") or "")

        self._peer_id_edit = QLineEdit()
        self._peer_id_edit.setPlaceholderText("Relay peer ID (required)")
        self._peer_id_edit.setText(str(_cfg.get("proxy_peer_id") or ""))

        settings_form.addRow("Local SOCKS5 port", self.listen_port)
        settings_form.addRow("Shared secret", self.proxy_secret)
        settings_form.addRow("Relay peer ID", self._peer_id_edit)

        save_btn = QPushButton("Save settings")
        save_btn.clicked.connect(self._save_settings)
        settings_form.addRow("", save_btn)

        self._settings_panel.setVisible(False)

        # ── Log panel ────────────────────────────────────────────────
        self._logs_toggle = QToolButton()
        self._logs_toggle.setText("Show log")
        self._logs_toggle.setCheckable(True)
        self._logs_toggle.toggled.connect(self._toggle_logs)

        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(800)
        self.logs.setPlaceholderText("Connection log will appear here.")
        self.logs.setVisible(False)
        self.logs.setMinimumHeight(120)

        # ── Layout assembly ───────────────────────────────────────────
        root.addWidget(title)
        root.addWidget(self._account_label)
        root.addWidget(self._status_card)
        root.addLayout(btn_row)
        root.addWidget(self._settings_toggle, 0, Qt.AlignmentFlag.AlignHCenter)
        root.addWidget(self._settings_panel)
        root.addWidget(self._logs_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        root.addWidget(self.logs, 1)
        root.addStretch(1)

        self._refresh_ui()

    # ── UI helpers ────────────────────────────────────────────────────

    def _toggle_settings(self, open_: bool) -> None:
        self._settings_toggle.setText("✕  Close settings" if open_ else "⚙  Settings")
        self._settings_panel.setVisible(open_)

    def _toggle_logs(self, open_: bool) -> None:
        self._logs_toggle.setText("Hide log" if open_ else "Show log")
        self.logs.setVisible(open_)

    def _save_settings(self) -> None:
        try:
            peer_id = int(self._peer_id_edit.text().strip()) if self._peer_id_edit.text().strip() else None
        except ValueError:
            peer_id = None
        save_proxy_config(
            peer_id=peer_id,
            proxy_secret=self.proxy_secret.text().strip() or None,
            listen_port=self.listen_port.value(),
        )
        self._append_log("Settings saved.")

    def _refresh_ui(self) -> None:
        busy = self._phase in {"connecting", "stopping"}
        active = self._phase in {"connecting", "connected"}
        editable = not active and not busy

        if self._phase == "connected":
            self._status_dot.setStyleSheet("font-size: 32px; color: #1f7a44;")
            self._status_text.setText("Connected")
        elif self._phase == "connecting":
            self._status_dot.setStyleSheet("font-size: 32px; color: #185c9c;")
            self._status_text.setText("Connecting…")
        elif self._phase == "stopping":
            self._status_dot.setStyleSheet("font-size: 32px; color: #6b7c77;")
            self._status_text.setText("Disconnecting…")
        elif self._phase == "error":
            self._status_dot.setStyleSheet("font-size: 32px; color: #b9384e;")
            self._status_text.setText("Connection failed")
        else:
            self._status_dot.setStyleSheet("font-size: 32px; color: #6b7c77;")
            self._status_text.setText("Disconnected")

        self.connect_btn.setText("Disconnect" if active else "Connect")
        self.connect_btn.setChecked(active)
        self.connect_btn.setEnabled(not busy)
        self.connect_btn.style().unpolish(self.connect_btn)
        self.connect_btn.style().polish(self.connect_btn)

        self.listen_port.setEnabled(editable)
        self.proxy_secret.setEnabled(editable)
        self._peer_id_edit.setEnabled(editable)

        # Update account label if phone changed
        if self._main.phone:
            self._account_label.setText(f"Signed in as +{self._main.phone}")

    def _set_phase(self, phase: str, detail: str = "") -> None:
        self._phase = phase
        self._refresh_ui()
        if detail:
            self._append_log(detail)

    def _append_log(self, line: str) -> None:
        secret = self.proxy_secret.text().strip()
        safe = line.replace(secret, "<secret>") if secret else line
        self.logs.appendPlainText(safe)

    # ── Session lifecycle ─────────────────────────────────────────────

    def _on_primary_clicked(self) -> None:
        if self._phase in {"connecting", "connected"}:
            self._stop_session()
        else:
            self._start_session()

    def _start_session(self) -> None:
        if not self._main.jwt:
            self._set_phase("error", "Sign in with your Bale phone number first.")
            return

        # Resolve relay peer_id from the field
        peer_id: Optional[int] = None
        try:
            raw = self._peer_id_edit.text().strip()
            if raw:
                peer_id = int(raw)
        except ValueError:
            pass

        if peer_id is None:
            self._set_phase("error", "Set a relay peer ID in Settings.")
            return

        self.logs.clear()
        self._proxy_addr.setVisible(False)

        worker = DirectProxyWorker(
            jwt=self._main.jwt,
            peer_id=peer_id,
            proxy_secret=self.proxy_secret.text().strip() or None,
            listen_host="127.0.0.1",
            listen_port=self.listen_port.value(),
            ws_ssl_no_verify=True,
        )
        worker.connecting.connect(self._on_connecting)
        worker.connected.connect(self._on_connected)
        worker.stopped.connect(self._on_stopped)
        worker.failed.connect(self._on_failed)
        worker.log_line.connect(self._append_log)
        self._worker = worker
        self._worker_thread = run_in_thread(worker)
        self._set_phase("connecting", "Starting secure connection…")

    def _stop_session(self) -> None:
        if self._worker is not None:
            self._worker.stop()
        self._set_phase("stopping", "Disconnecting…")

    def _on_connecting(self, msg: str) -> None:
        self._status_text.setText(msg[:60])
        self._append_log(msg)

    def _on_connected(self, addr: str) -> None:
        self._phase = "connected"
        self._refresh_ui()
        self._proxy_addr.setText(f"SOCKS5 proxy active on  {addr}\nSet your browser or system to use SOCKS5 127.0.0.1:{self.listen_port.value()}")
        self._proxy_addr.setVisible(True)
        self._append_log(f"Connected — {addr}")

    def _on_stopped(self) -> None:
        self._worker = None
        self._worker_thread = None
        self._proxy_addr.setVisible(False)
        self._set_phase("idle", "Disconnected.")

    def _on_failed(self, err: str) -> None:
        self._worker = None
        self._worker_thread = None
        self._proxy_addr.setVisible(False)
        self._set_phase("error", friendly_error_message(err, context="connect"))
        self._append_log("FAIL " + err)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("baleobala")
        self.resize(520, 640)
        self.setMinimumSize(400, 520)
        self.setAutoFillBackground(True)
        self.setStyleSheet("QMainWindow { background: #f4f8f7; color: #10211f; }")

        self._service = ControlService()
        self.jwt: Optional[str] = None
        self.phone: Optional[str] = None

        content = QWidget(objectName="container")
        content.setAutoFillBackground(True)
        content.setStyleSheet("background-color: #f4f8f7;")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        self.login_view = LoginView(self)
        self.connect_view = ConnectView(self)
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

        handler = QtLogHandler(level=logging.WARNING)
        handler.line.connect(self.connect_view._append_log)
        logging.getLogger("baleobala").addHandler(handler)

    @property
    def service(self) -> ControlService:
        return self._service

    def set_jwt(self, jwt: str, *, phone: Optional[str] = None) -> None:
        self.jwt = jwt
        self.phone = phone
        self._update_status()
        self.connect_view._refresh_ui()

    def show_connect_view(self) -> None:
        self.login_view.setVisible(False)
        self.connect_view.setVisible(True)
        self._logout_action.setVisible(True)
        self.connect_view._refresh_ui()

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
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Sign out of Bale?")
        box.setText("Sign out of Bale?")
        box.setInformativeText(
            "You'll need to enter your phone number again to sign back in."
        )
        sign_out_btn = box.addButton("Sign out", QMessageBox.ButtonRole.DestructiveRole)
        cancel_btn = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel_btn)
        box.exec()
        if box.clickedButton() is not sign_out_btn:
            return
        try:
            self._service.clear_auth()
        except Exception as e:
            log.warning("clear failed: %s", e)
        self.jwt = None
        self.phone = None
        self._update_status()
        self.show_login_view()
        self.connect_view._set_phase("idle", "Sign in to connect.")

    def closeEvent(self, event) -> None:  # noqa: ANN001
        worker = self.connect_view._worker
        if worker is not None:
            try:
                worker.stop()
            except Exception:
                pass
        thread = self.connect_view._worker_thread
        if thread is not None:
            thread.quit()
            thread.wait(2500)
        super().closeEvent(event)


def run(argv: Optional[list[str]] = None) -> int:
    _configure_gui_logging()
    app = QApplication(argv or sys.argv)
    app.setApplicationName("baleobala")
    apply_theme(app, mode="light")
    win = MainWindow()
    win.show()
    return app.exec()
