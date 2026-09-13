from __future__ import annotations

import threading
import time
from dataclasses import replace

from userbot_bale.control.credential_watcher import CredentialWatcher
from userbot_bale.control.pairing import PairingRecord


class _FakePairingStore:
    def __init__(self, record: PairingRecord) -> None:
        self.record = record

    def get(self, profile_id: str) -> PairingRecord | None:  # noqa: ARG002
        return self.record


class _FakeControlService:
    def __init__(self, store: _FakePairingStore, *, fail: bool = False) -> None:
        self.store = store
        self.fail = fail
        self.refresh_calls = 0

    def refresh_pairing_credentials(self, profile_id: str) -> PairingRecord:
        self.refresh_calls += 1
        if self.fail:
            raise RuntimeError("refresh failed")
        self.store.record = replace(
            self.store.record,
            credential_refresh_after=time.time() + 3600,
            credential_expires_at=time.time() + 7200,
        )
        return self.store.record


def _make_record(*, refresh_after: float, expires_at: float) -> PairingRecord:
    return PairingRecord(
        profile_id="profile-a",
        name="relay",
        role="client",
        pair_code="code",
        credential_refresh_after=refresh_after,
        credential_expires_at=expires_at,
    )


def test_credential_watcher_refreshes_soon_after_deadline() -> None:
    record = _make_record(refresh_after=time.time() - 1, expires_at=time.time() + 3600)
    store = _FakePairingStore(record)
    service = _FakeControlService(store)
    refreshed = threading.Event()

    watcher = CredentialWatcher(
        store,
        service,
        "profile-a",
        lambda updated: refreshed.set(),
        poll_interval=0.05,
    )
    watcher.start()
    assert refreshed.wait(timeout=2.0)
    watcher.stop()

    assert service.refresh_calls == 1


def test_credential_watcher_emits_expired_after_failed_refresh() -> None:
    record = _make_record(refresh_after=time.time() - 1, expires_at=time.time() - 1)
    store = _FakePairingStore(record)
    service = _FakeControlService(store, fail=True)
    expired = threading.Event()

    watcher = CredentialWatcher(
        store,
        service,
        "profile-a",
        lambda _updated: None,
        on_expired=lambda _record: expired.set(),
        poll_interval=0.05,
    )
    watcher.start()
    assert expired.wait(timeout=2.0)
    watcher.stop()

    assert service.refresh_calls >= 1
