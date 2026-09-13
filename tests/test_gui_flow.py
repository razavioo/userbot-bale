from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture(autouse=True)
def _cleanup_qt(qapp):
    yield
    for widget in list(qapp.topLevelWidgets()):
        widget.close()
        widget.deleteLater()
    qapp.processEvents()


def test_control_plane_connect_worker_requires_pairing() -> None:
    from userbot_bale.gui.workers import ControlPlaneConnectWorker

    class FakeService:
        def reconcile_runtime(self):
            from types import SimpleNamespace

            return SimpleNamespace(pairing=None)

    worker = ControlPlaneConnectWorker(FakeService())
    failures: list[str] = []
    worker.failed.connect(failures.append)

    worker.run()

    assert failures
    assert "No paired relay" in failures[0]


def test_status_poller_emits_snapshot() -> None:
    from types import SimpleNamespace

    from userbot_bale.gui.workers import StatusPoller

    snapshots: list[dict] = []

    class FakeService:
        def __init__(self) -> None:
            self.calls = 0

        def status(self):
            self.calls += 1
            if self.calls == 1:
                worker.stop()
            return SimpleNamespace(
                auth={"state": "configured"},
                vpn={"state": "configured"},
                pairing={
                    "state": "paired",
                    "name": "home-relay",
                    "credential_expires_at": "4102444800",
                },
                backend={
                    "backend": "linux-tun",
                    "state": "running",
                    "transport_selected": "dc",
                    "recovery_state": "healthy",
                    "failover_count": "2",
                    "carrier_session_id": "sess-1",
                    "carrier_latency_ms": "12",
                },
                connection={"state": "ready"},
            )

    worker = StatusPoller(FakeService(), interval_seconds=0.01)
    worker.snapshot.connect(snapshots.append)
    worker.run()

    assert snapshots
    assert snapshots[0]["backend"]["transport_selected"] == "dc"
    assert snapshots[0]["backend"]["failover_count"] == "2"


def test_connect_view_saves_and_loads_proxy_settings(qapp, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("USERBOT_BALE_HOME", str(tmp_path))
    from userbot_bale.control.proxy_config import load_proxy_config
    from userbot_bale.gui.app import MainWindow

    win = MainWindow()
    view = win.connect_view
    view._peer_id_edit.setText("123456")
    view.proxy_secret.setText("my-secret")
    view.listen_port.setValue(10888)
    view._save_settings()

    cfg = load_proxy_config()
    assert cfg.get("proxy_peer_id") == 123456
    assert cfg.get("proxy_secret") == "my-secret"
    assert cfg.get("listen_port") == 10888


def test_connect_view_updates_status_card_per_phase(qapp, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("USERBOT_BALE_HOME", str(tmp_path))
    from userbot_bale.gui.app import MainWindow

    win = MainWindow()
    view = win.connect_view
    assert view._status_text.text() == "Disconnected"
    assert view.connect_btn.text() == "Connect"

    view._set_phase("connecting", "Starting...")
    assert view._status_text.text() == "Connecting…"
    assert view.connect_btn.text() == "Disconnect"

    view._set_phase("connected", "Ready")
    assert view._status_text.text() == "Connected"

    view._set_phase("error", "Failed")
    assert view._status_text.text() == "Connection failed"


def test_login_view_validates_phone_and_code(qapp, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("USERBOT_BALE_HOME", str(tmp_path))
    from userbot_bale.gui.app import MainWindow

    win = MainWindow()
    view = win.login_view

    view.phone.setText("12")
    view._on_send_clicked()
    assert "Phone number needs attention" in view.banner.title.text()

    view.code.setEnabled(True)
    view.code.setText("abc")
    view._on_verify_clicked()
    assert "Code needs attention" in view.banner.title.text()


def test_connect_view_keeps_logs_collapsed_by_default(qapp, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("USERBOT_BALE_HOME", str(tmp_path))
    from userbot_bale.gui.app import MainWindow

    win = MainWindow()

    assert not win.connect_view.logs.isVisible()
    assert win.connect_view._logs_toggle.text() == "Show log"


def test_connect_view_toggle_settings_and_logs(qapp, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("USERBOT_BALE_HOME", str(tmp_path))
    from userbot_bale.gui.app import MainWindow

    win = MainWindow()
    view = win.connect_view

    assert view._settings_panel.isHidden()
    view._settings_toggle.setChecked(True)
    assert not view._settings_panel.isHidden()

    assert view.logs.isHidden()
    view._logs_toggle.setChecked(True)
    assert not view.logs.isHidden()


def test_login_view_includes_onboarding_panel(qapp, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("USERBOT_BALE_HOME", str(tmp_path))
    from PySide6.QtWidgets import QFrame
    from userbot_bale.gui.app import MainWindow

    win = MainWindow()
    panels = [panel for panel in win.login_view.findChildren(QFrame) if panel.objectName() == "hero-panel"]
    assert panels
