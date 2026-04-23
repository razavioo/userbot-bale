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
    from baleobala.gui.workers import ControlPlaneConnectWorker

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

    from baleobala.gui.workers import StatusPoller

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


def test_connect_view_creates_pairing_and_persists_profile(qapp, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))

    from baleobala.gui.app import MainWindow

    win = MainWindow()
    win.set_jwt("jwt-token", phone="+989")

    created: dict[str, object] = {}

    def fake_begin_pairing(name, **kwargs):  # noqa: ANN001
        from types import SimpleNamespace

        created["pairing"] = (name, kwargs)
        return SimpleNamespace(profile_id="pair-1", pair_code="abc123")

    def fake_ensure_profile():
        from types import SimpleNamespace

        return SimpleNamespace(
            profile_id="default",
            name="default",
            backend="packet-tunnel",
            role="client",
            pairing_id=None,
            peer_id=None,
            peer_name=None,
            answer=False,
            auto_start=False,
            listen_host="127.0.0.1",
            listen_port=1080,
            protocol="fast",
            volume=50,
            proxy_secret=None,
        )

    def fake_save_profile(profile):  # noqa: ANN001
        created["profile"] = profile

    monkeypatch.setattr(win.service, "load_pairing", lambda profile_id=None: None)
    monkeypatch.setattr(win.service, "begin_pairing", fake_begin_pairing)
    monkeypatch.setattr(win.service, "ensure_profile", fake_ensure_profile)
    monkeypatch.setattr(win.service, "save_profile", fake_save_profile)

    view = win.connect_view
    view.pair_name.setText("home-relay")
    view.proxy_secret.setText("secret")
    view._ensure_pairing(pair_name="home-relay", pair_code="", role="client")
    view._persist_profile_defaults(pair_name="home-relay", role="client", backend="packet-tunnel")

    assert created["pairing"][0] == "home-relay"
    profile = created["profile"]
    assert profile.name == "home-relay"
    assert profile.backend == "packet-tunnel"
    assert profile.proxy_secret == "secret"


def test_connect_view_gate_action_creates_pairing(qapp, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.gui.app import MainWindow

    win = MainWindow()
    win.set_jwt("jwt-token", phone="+989")

    created: dict[str, object] = {}

    def fake_begin_pairing(name, **kwargs):  # noqa: ANN001
        from types import SimpleNamespace

        created["pairing"] = (name, kwargs)
        return SimpleNamespace(profile_id="pair-1", pair_code="abc123")

    def fake_ensure_profile():
        from types import SimpleNamespace

        return SimpleNamespace(
            profile_id="default",
            name="default",
            backend="packet-tunnel",
            role="client",
            pairing_id=None,
            peer_id=None,
            peer_name=None,
            answer=False,
            auto_start=False,
            listen_host="127.0.0.1",
            listen_port=1080,
            protocol="fast",
            volume=50,
            proxy_secret=None,
        )

    def fake_save_profile(profile):  # noqa: ANN001
        created["profile"] = profile

    monkeypatch.setattr(win.service, "load_pairing", lambda profile_id=None: None)
    monkeypatch.setattr(win.service, "begin_pairing", fake_begin_pairing)
    monkeypatch.setattr(win.service, "ensure_profile", fake_ensure_profile)
    monkeypatch.setattr(win.service, "save_profile", fake_save_profile)

    view = win.connect_view
    view._latest_snapshot = {
        "auth": {"state": "configured"},
        "vpn": {"state": "configured"},
        "pairing": {},
        "backend": {"backend": "packet-tunnel", "state": "stopped"},
        "connection": {
            "state": "blocked",
            "code": "pairing_missing",
            "title": "Relay pairing needed",
            "message": "No relay pairing is ready yet.",
            "next_step": "Create a pairing first.",
        },
    }
    view.pair_name.setText("home-relay")
    view.backend.setCurrentIndex(1)
    view._on_gate_action_clicked()

    assert created["pairing"][0] == "home-relay"
    profile = created["profile"]
    assert profile.name == "home-relay"
    assert view.setup_toggle.isChecked()


def test_login_view_validates_phone_and_code(qapp, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.gui.app import MainWindow

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
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.gui.app import MainWindow

    win = MainWindow()

    assert not win.connect_view.logs.isVisible()
    assert win.connect_view.logs_toggle.text() == "Show Connection Log"


def test_connect_view_shows_readiness_items(qapp, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.gui.app import MainWindow

    win = MainWindow()

    labels = [label.text() for label in win.connect_view.readiness._labels]
    assert any("Bale sign-in" in text for text in labels)
    assert any("Relay pairing" in text for text in labels)


def test_login_view_includes_onboarding_panel(qapp, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from PySide6.QtWidgets import QFrame
    from baleobala.gui.app import MainWindow

    win = MainWindow()
    panels = [panel for panel in win.login_view.findChildren(QFrame) if panel.objectName() == "hero-panel"]
    assert panels


def test_connect_view_surfaces_failover_status(qapp, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.gui.app import MainWindow

    win = MainWindow()
    win.connect_view._on_connected(
        {
            "backend": {
                "backend": "linux-tun",
                "transport_selected": "audio",
                "transport_previous": "dc",
                "recovery_state": "recovering",
                "failover_count": "2",
                "peer_coordination": "active",
            },
            "probe": {"ok": "yes", "detail": "probe reached gateway"},
        }
    )

    detail = win.connect_view.phase_detail.text()
    assert "recovering via linux-tun" in detail.lower()
    assert "Previous transport: dc." in detail
    assert "Failovers so far: 2." in detail
    assert "Peer failover coordination is active." in detail
