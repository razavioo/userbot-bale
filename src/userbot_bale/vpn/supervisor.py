"""Supervised VPN runner with session checkpointing and auto-reconnect.

Wraps a :class:`VpnRunner` with a retry loop driven by a transport factory.
When the current transport closes (network blip, peer hang-up, ARQ death)
the supervisor backs off exponentially, builds a new transport, and starts
a new runner — without tearing down the TUN device or re-authing.

Checkpoint is a tiny JSON document (peer, session id, last-start time) that
survives process restarts; on cold start, the CLI/GUI can reload it to
decide whether to auto-resume.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from userbot_bale.control.paths import config_dir
from userbot_bale.control.observability import StructuredEventRecorder, classify_failure
from userbot_bale.control.store import JsonStore

from .runner import RunnerConfig, VpnRunner
from .transports import Transport
from .tun import TunDevice

log = logging.getLogger(__name__)


@dataclass
class SessionCheckpoint:
    peer: str | None = None
    sess_id: int = 0
    backend: str = ""
    tun_name: str = ""
    started_at: float = field(default_factory=time.time)
    attempts: int = 0
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionCheckpoint":
        return cls(
            peer=data.get("peer"),
            sess_id=int(data.get("sess_id", 0)),
            backend=str(data.get("backend", "")),
            tun_name=str(data.get("tun_name", "")),
            started_at=float(data.get("started_at", time.time())),
            attempts=int(data.get("attempts", 0)),
            last_error=data.get("last_error"),
        )


@dataclass
class SupervisorConfig:
    initial_backoff: float = 0.5
    max_backoff: float = 30.0
    backoff_factor: float = 2.0
    max_retries: int | None = None  # None = retry forever
    restart_window: float = 60.0  # attempts within this many seconds count toward retries


class SupervisedRunner:
    """Runs a :class:`VpnRunner` and restarts it on transport loss."""

    def __init__(
        self,
        tun: TunDevice,
        transport_factory: Callable[[], Transport],
        *,
        runner_config: RunnerConfig | None = None,
        supervisor_config: SupervisorConfig | None = None,
        checkpoint_path: Path | None = None,
        checkpoint: SessionCheckpoint | None = None,
        recorder: StructuredEventRecorder | None = None,
    ) -> None:
        self._tun = tun
        self._transport_factory = transport_factory
        self._runner_config = runner_config or RunnerConfig()
        self._cfg = supervisor_config or SupervisorConfig()
        self._checkpoint = checkpoint or SessionCheckpoint(
            sess_id=self._runner_config.sess_id,
            tun_name=tun.name,
        )
        self._store = JsonStore(checkpoint_path or (config_dir() / "vpn_session.json"))
        self._runner: VpnRunner | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._recorder = recorder or StructuredEventRecorder(component="vpn-supervisor")

    @property
    def checkpoint(self) -> SessionCheckpoint:
        return self._checkpoint

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("supervisor already started")
        self._stop.clear()
        self._thread = threading.Thread(target=self._supervise, name="vpn-supervisor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._runner is not None:
            try:
                self._runner.stop()
            except Exception:  # noqa: BLE001
                log.exception("runner stop failed")
            self._runner = None
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        try:
            self._store.path.unlink()
        except FileNotFoundError:
            pass

    def _supervise(self) -> None:
        attempts = 0
        window_start = time.time()
        backoff = self._cfg.initial_backoff
        while not self._stop.is_set():
            try:
                transport = self._transport_factory()
            except Exception as e:  # noqa: BLE001
                info = classify_failure(last_error="transport factory failed: " + str(e))
                self._recorder.event(
                    "transport_setup_failed",
                    stage="transport_init",
                    outcome="failure",
                    attempt=attempts + 1,
                    failure_class=info.failure_class,
                    failure_code=info.failure_code,
                    error_type=type(e).__name__,
                    error_message=str(e),
                )
                log.warning("transport factory failed: %s", e)
                self._checkpoint.last_error = str(e)
                self._persist()
                self._recorder.event(
                    "supervisor_retry_scheduled",
                    stage="transport_runtime",
                    outcome="begin",
                    attempt=attempts + 1,
                    error_message=str(e),
                )
                if not self._wait_backoff(backoff):
                    return
                backoff = min(backoff * self._cfg.backoff_factor, self._cfg.max_backoff)
                continue

            runner = VpnRunner(self._tun, transport, self._runner_config)
            self._runner = runner
            self._checkpoint.attempts = attempts
            self._checkpoint.last_error = None
            self._persist()
            try:
                self._recorder.event("runner_started", stage="transport_runtime", outcome="success", attempt=attempts)
                runner.start()
                runner.join()  # blocks until tun or transport closes
            except Exception as e:  # noqa: BLE001
                info = classify_failure(ready=True, last_error=str(e))
                self._recorder.event(
                    "runner_crashed",
                    stage="transport_runtime",
                    outcome="failure",
                    attempt=attempts,
                    failure_class=info.failure_class,
                    failure_code=info.failure_code,
                    error_type=type(e).__name__,
                    error_message=str(e),
                )
                log.warning("runner crashed: %s", e)
                self._checkpoint.last_error = str(e)
            finally:
                try:
                    runner.stop()
                except Exception:  # noqa: BLE001
                    pass
                self._runner = None

            if self._stop.is_set():
                return

            # Transport died; decide whether to retry.
            now = time.time()
            if now - window_start > self._cfg.restart_window:
                attempts = 0
                window_start = now
                backoff = self._cfg.initial_backoff
            attempts += 1
            if self._cfg.max_retries is not None and attempts > self._cfg.max_retries:
                self._recorder.event(
                    "supervisor_give_up",
                    stage="transport_runtime",
                    outcome="failure",
                    attempt=attempts,
                    error_message=self._checkpoint.last_error,
                )
                log.error("supervisor giving up after %d attempts", attempts)
                return
            self._recorder.event(
                "supervisor_retry_scheduled",
                stage="transport_runtime",
                outcome="begin",
                attempt=attempts,
                error_message=self._checkpoint.last_error,
            )
            log.info(
                "transport closed; reconnect in %.1fs (attempt %d)", backoff, attempts
            )
            if not self._wait_backoff(backoff):
                return
            backoff = min(backoff * self._cfg.backoff_factor, self._cfg.max_backoff)

    def _wait_backoff(self, seconds: float) -> bool:
        """Sleep for ``seconds`` unless stopped. Returns False if stopped."""
        return not self._stop.wait(seconds)

    def _persist(self) -> None:
        try:
            self._store.save(self._checkpoint.to_dict())
        except OSError as exc:
            # Don't swallow silently — a checkpoint write failure means
            # the next restart may resume from stale state.
            log.warning("supervisor: failed to persist checkpoint: %s", exc)


def load_checkpoint(path: Path | None = None) -> SessionCheckpoint | None:
    store = JsonStore(path or (config_dir() / "vpn_session.json"))
    payload = store.load(default=None)
    if not isinstance(payload, dict):
        return None
    try:
        return SessionCheckpoint.from_dict(payload)
    except (TypeError, ValueError):
        return None
