"""Background refresh loop for expiring pairing credentials."""

from __future__ import annotations

import threading
import time
from typing import Callable

from userbot_bale.control.pairing import PairingRecord, PairingStore


RefreshCallback = Callable[[PairingRecord], None]


class CredentialWatcher:
    """Watch pairing credentials and refresh them before expiry."""

    def __init__(
        self,
        pairing_store: PairingStore,
        control_service,
        profile_id: str,
        on_refreshed: RefreshCallback,
        *,
        on_expired: RefreshCallback | None = None,
        poll_interval: float = 300.0,
    ) -> None:
        self._pairing_store = pairing_store
        self._control_service = control_service
        self._profile_id = profile_id
        self._on_refreshed = on_refreshed
        self._on_expired = on_expired
        self._poll_interval = max(1.0, float(poll_interval))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._expired_notified = False

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="credential-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
            self._thread = None

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def _run(self) -> None:
        while not self._stop.is_set():
            record = self._pairing_store.get(self._profile_id)
            if record is None:
                self._wait(self._poll_interval)
                continue

            now = time.time()
            refresh_after = record.credential_refresh_after
            expires_at = record.credential_expires_at

            if refresh_after is not None and refresh_after <= now:
                try:
                    refreshed = self._control_service.refresh_pairing_credentials(self._profile_id)
                except Exception:
                    if (
                        expires_at is not None
                        and expires_at <= now
                        and self._on_expired is not None
                        and not self._expired_notified
                    ):
                        self._expired_notified = True
                        self._on_expired(record)
                    self._wait(min(self._poll_interval, 5.0))
                    continue
                self._expired_notified = False
                self._on_refreshed(refreshed)
                self._wait(self._poll_interval)
                continue

            self._wait(self._sleep_interval(record, now))

    def _sleep_interval(self, record: PairingRecord, now: float) -> float:
        refresh_after = record.credential_refresh_after
        if refresh_after is None:
            return self._poll_interval
        return max(0.0, min(self._poll_interval, refresh_after - now))

    def _wait(self, timeout: float) -> None:
        self._stop.wait(timeout=max(0.0, timeout))
