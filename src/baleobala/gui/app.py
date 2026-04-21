"""Main Qt window — login view → connect view."""

from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from typing import Optional

from PySide6.QtCore import Qt, QThread
from PySide6.QtGui import QAction, QFont, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QStatusBar,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from baleobala.control import ControlService
from baleobala.control.paths import data_dir
from baleobala.gui.theme import apply_theme
from baleobala.gui.workers import (
    ProxyWorker,
    QtLogHandler,
    StartSmsWorker,
    ValidateCodeWorker,
    run_in_thread,
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


def _hline() -> QFrame:
    f = QFrame()
    f.setObjectName("divider")
    f.setFrameShape(QFrame.Shape.HLine)
    return f


def _section(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("section")
    return lbl


# ---------------------------------------------------------------------------
# Login view
# ---------------------------------------------------------------------------


class LoginView(QWidget):
    def __init__(self, parent: "MainWindow") -> None:
        super().__init__(parent)
        self._main = parent
        self._sms_worker: Optional[StartSmsWorker] = None
        self._validate_worker: Optional[ValidateCodeWorker] = None
        self._auth_handle = None

        card = QWidget(objectName="card")
        card.setMaximumWidth(460)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(32, 28, 32, 28)
        card_layout.setSpacing(14)

        title = QLabel("Sign in to Bale")
        title.setObjectName("title")
        subtitle = QLabel("We'll text a 5-digit code to your phone. No browser needed.")
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)

        phone_label = QLabel("Phone number")
        self.phone = QLineEdit()
        self.phone.setPlaceholderText("+98 912 345 6789")
        self.phone.setClearButtonEnabled(True)

        self.send_btn = QPushButton("Send SMS code")
        self.send_btn.setObjectName("primary")
        self.send_btn.setDefault(True)
        self.send_btn.clicked.connect(self._on_send_clicked)

        code_label = QLabel("SMS code")
        self.code = QLineEdit()
        self.code.setPlaceholderText("12345")
        self.code.setEnabled(False)
        self.code.setMaxLength(8)

        self.verify_btn = QPushButton("Verify & continue")
        self.verify_btn.setObjectName("primary")
        self.verify_btn.setEnabled(False)
        self.verify_btn.clicked.connect(self._on_verify_clicked)

        self.status = QLabel(" ")
        self.status.setObjectName("status-info")
        self.status.setWordWrap(True)

        card_layout.addWidget(title)
        card_layout.addWidget(subtitle)
        card_layout.addSpacing(6)
        card_layout.addWidget(phone_label)
        card_layout.addWidget(self.phone)
        card_layout.addWidget(self.send_btn)
        card_layout.addWidget(_hline())
        card_layout.addWidget(code_label)
        card_layout.addWidget(self.code)
        card_layout.addWidget(self.verify_btn)
        card_layout.addWidget(self.status)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(24, 40, 24, 40)
        outer.addStretch(1)
        v = QVBoxLayout()
        v.addStretch(1)
        v.addWidget(card)
        v.addStretch(2)
        outer.addLayout(v)
        outer.addStretch(1)

    # ---- actions ----

    def _set_status(self, text: str, kind: str = "info") -> None:
        self.status.setText(text)
        self.status.setObjectName(
            {"info": "status-info", "ok": "status-ok", "err": "status-err"}[kind]
        )
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def _on_send_clicked(self) -> None:
        phone = self.phone.text().strip()
        if not phone:
            self._set_status("Enter a phone number first.", "err")
            return
        self.send_btn.setEnabled(False)
        self._set_status("Sending SMS…", "info")

        worker = StartSmsWorker(phone)
        worker.ok.connect(self._on_sms_sent)
        worker.failed.connect(self._on_sms_failed)
        self._sms_worker = worker
        run_in_thread(worker)

    def _on_sms_sent(self, tx: str) -> None:
        self._auth_handle = self._sms_worker.auth_handle() if self._sms_worker else None
        self._set_status(f"SMS sent. Enter the code below.", "ok")
        self.code.setEnabled(True)
        self.verify_btn.setEnabled(True)
        self.send_btn.setEnabled(True)
        self.send_btn.setText("Resend SMS")
        self.code.setFocus()

    def _on_sms_failed(self, err: str) -> None:
        self.send_btn.setEnabled(True)
        self._set_status(f"Failed: {err}", "err")

    def _on_verify_clicked(self) -> None:
        code = self.code.text().strip()
        if not code or self._auth_handle is None:
            return
        self.verify_btn.setEnabled(False)
        self._set_status("Verifying code…", "info")

        worker = ValidateCodeWorker(self._auth_handle, code)
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
        self._set_status("Signed in.", "ok")
        self._main.set_jwt(jwt, phone=phone)
        self._main.show_connect_view()

    def _on_verify_failed(self, err: str) -> None:
        self.verify_btn.setEnabled(True)
        self._set_status(f"Verify failed: {err}", "err")


# ---------------------------------------------------------------------------
# Connect view
# ---------------------------------------------------------------------------


class ConnectView(QWidget):
    def __init__(self, parent: "MainWindow") -> None:
        super().__init__(parent)
        self._main = parent
        self._proxy_worker: Optional[ProxyWorker] = None
        self._proxy_thread: Optional[QThread] = None

        header = QLabel("Connect")
        header.setObjectName("title")

        # ---- Session section ----
        self.role = QComboBox()
        self.role.addItem("Client — run a local SOCKS5 proxy", "client")
        self.role.addItem("Relay — exit node for a client", "relay")
        self.role.currentIndexChanged.connect(self._refresh_enabled)

        self.dial_mode = QComboBox()
        self.dial_mode.addItem("Dial a contact by name", "name")
        self.dial_mode.addItem("Answer incoming call", "answer")
        self.dial_mode.currentIndexChanged.connect(self._refresh_enabled)

        self.peer_name = QLineEdit()
        self.peer_name.setPlaceholderText("Bale contact display name")

        session_form = QFormLayout()
        session_form.setHorizontalSpacing(14)
        session_form.setVerticalSpacing(8)
        session_form.addRow("Role", self.role)
        session_form.addRow("How to connect", self.dial_mode)
        session_form.addRow("Peer name", self.peer_name)

        # ---- Proxy section ----
        self.proxy_secret = QLineEdit()
        self.proxy_secret.setPlaceholderText("shared secret (both sides match)")
        self.proxy_secret.setEchoMode(QLineEdit.EchoMode.Password)

        self.listen_port = QSpinBox()
        self.listen_port.setRange(1024, 65535)
        self.listen_port.setValue(1080)

        proxy_form = QFormLayout()
        proxy_form.setHorizontalSpacing(14)
        proxy_form.setVerticalSpacing(8)
        proxy_form.addRow("Shared secret", self.proxy_secret)
        proxy_form.addRow("SOCKS5 port", self.listen_port)

        # ---- Buttons ----
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("primary")
        self.connect_btn.setDefault(True)
        self.connect_btn.clicked.connect(self._on_connect_clicked)

        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.setObjectName("danger")
        self.disconnect_btn.setEnabled(False)
        self.disconnect_btn.clicked.connect(self._on_disconnect_clicked)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.connect_btn)
        btn_row.addWidget(self.disconnect_btn)
        btn_row.addStretch(1)

        # ---- Status + Logs ----
        self.state_label = QLabel("Idle.")
        self.state_label.setObjectName("status-info")

        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(1500)
        self.logs.setPlaceholderText("Logs will appear here once connected.")

        # ---- Assemble ----
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(10)
        layout.addWidget(header)
        layout.addSpacing(6)
        layout.addWidget(_section("Session"))
        layout.addLayout(session_form)
        layout.addSpacing(6)
        layout.addWidget(_section("Proxy"))
        layout.addLayout(proxy_form)
        layout.addSpacing(6)
        layout.addLayout(btn_row)
        layout.addWidget(self.state_label)
        layout.addWidget(_section("Logs"))
        layout.addWidget(self.logs, 1)

        self._refresh_enabled()

    def _refresh_enabled(self) -> None:
        answering = self.dial_mode.currentData() == "answer"
        self.peer_name.setEnabled(not answering)
        is_client = self.role.currentData() == "client"
        self.listen_port.setEnabled(is_client)

    def _set_state(self, text: str, kind: str = "info") -> None:
        self.state_label.setText(text)
        self.state_label.setObjectName(
            {"info": "status-info", "ok": "status-ok", "err": "status-err"}[kind]
        )
        self.state_label.style().unpolish(self.state_label)
        self.state_label.style().polish(self.state_label)

    # ---- actions ----

    def _on_connect_clicked(self) -> None:
        jwt = self._main.jwt
        if not jwt:
            QMessageBox.warning(self, "Not signed in", "Please sign in first.")
            return

        role = self.role.currentData()
        answering = self.dial_mode.currentData() == "answer"
        peer_name = self.peer_name.text().strip()
        if not answering and not peer_name:
            self._set_state("Enter a peer name, or choose Answer mode.", "err")
            return

        worker = ProxyWorker(
            role=role,
            jwt=jwt,
            peer_name=peer_name if not answering else "",
            answer=answering,
            dial_timeout=120.0,
            listen_port=self.listen_port.value(),
            proxy_secret=self.proxy_secret.text().strip() or None,
        )
        worker.connecting.connect(self._on_connecting)
        worker.connected.connect(self._on_connected)
        worker.stopped.connect(self._on_stopped)
        worker.failed.connect(self._on_failed)
        worker.log_line.connect(self._append_log)

        self._proxy_worker = worker
        self._proxy_thread = run_in_thread(worker)

        self.connect_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(True)
        self._set_state("Starting…", "info")
        self.logs.clear()

    def _on_disconnect_clicked(self) -> None:
        if self._proxy_worker:
            self._proxy_worker.stop()
        self.disconnect_btn.setEnabled(False)
        self._set_state("Stopping…", "info")

    # ---- signals from worker ----

    def _on_connecting(self, msg: str) -> None:
        self._set_state(msg, "info")
        self._append_log(msg)

    def _on_connected(self, msg: str) -> None:
        self._set_state("Connected — " + msg, "ok")
        self._append_log(msg)

    def _on_stopped(self) -> None:
        self._set_state("Disconnected.", "info")
        self.connect_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(False)
        self._append_log("stopped")

    def _on_failed(self, err: str) -> None:
        self._set_state(f"Failed: {err}", "err")
        self.connect_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(False)
        self._append_log("FAIL " + err)

    def _append_log(self, line: str) -> None:
        self.logs.appendPlainText(line)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("baleobala")
        self.resize(820, 640)

        self._service = ControlService()
        self.jwt: Optional[str] = None
        self.phone: Optional[str] = None

        self.stack = QStackedWidget()
        self.login_view = LoginView(self)
        self.connect_view = ConnectView(self)
        self.stack.addWidget(self.login_view)
        self.stack.addWidget(self.connect_view)

        container = QWidget(objectName="container")
        cv = QVBoxLayout(container)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.addWidget(self.stack)
        self.setCentralWidget(container)

        status = QStatusBar()
        self.setStatusBar(status)
        self._status_label = QLabel()
        status.addWidget(self._status_label, 1)

        self._logout_action = QAction("Sign out", self)
        self._logout_action.triggered.connect(self._on_logout)
        self.menuBar().addAction(self._logout_action)

        self._update_status()

        try:
            record = self._service.load_auth()
        except Exception as e:
            log.warning("auth load failed: %s", e)
            record = None
        if record and record.jwt:
            self.set_jwt(record.jwt, phone=record.phone)
            self.show_connect_view()

        handler = QtLogHandler(level=logging.INFO)
        handler.line.connect(self.connect_view._append_log)
        logging.getLogger("baleobala").addHandler(handler)

    def set_jwt(self, jwt: str, *, phone: Optional[str] = None) -> None:
        self.jwt = jwt
        self.phone = phone
        self._update_status()

    def show_connect_view(self) -> None:
        self.stack.setCurrentWidget(self.connect_view)
        self._logout_action.setVisible(True)

    def show_login_view(self) -> None:
        self.stack.setCurrentWidget(self.login_view)
        self._logout_action.setVisible(False)

    def _update_status(self) -> None:
        if self.jwt:
            who = ("+" + self.phone) if self.phone else "signed in"
            self._status_label.setText(f"● Signed in as {who}")
        else:
            self._status_label.setText("○ Not signed in")

    @property
    def service(self) -> ControlService:
        return self._service

    def _on_logout(self) -> None:
        try:
            self._service.clear_auth()
        except Exception as e:
            log.warning("clear failed: %s", e)
        self.jwt = None
        self.phone = None
        self._update_status()
        self.show_login_view()


def run(argv: Optional[list[str]] = None) -> int:
    _configure_gui_logging()
    app = QApplication(argv or sys.argv)
    app.setApplicationName("baleobala")
    apply_theme(app, mode="auto")
    win = MainWindow()
    win.show()
    return app.exec()
