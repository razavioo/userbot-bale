"""
High-level runner that glues TUN device ↔ Tunnel ↔ Transport.

Two worker threads:
    * tun_rx_thread: reads IP packets from the TUN device, hands them
      to tunnel.send_packet() (blocks on ARQ window).
    * tunnel → tun: the tunnel's own receive thread calls on_packet=
      tun.write_packet directly.

stop() joins cleanly. This runner is transport-agnostic — the caller
constructs the Transport (DataChannelTransport, AudioTransport, …) and
hands it in.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Callable

from .qos import PriorityQueue
from .transports import Transport
from .tun import TunDevice
from .tunnel import Tunnel

log = logging.getLogger(__name__)


@dataclass
class RunnerConfig:
    sess_id: int = 0x1111
    window: int = 32
    ack_timeout: float = 0.5
    max_retries: int = 8
    # When set, the tunnel's frame cap is fixed at this value regardless
    # of the current transport's self-declared MTU. Required for
    # hot-swap: a packet fragmented for a 14 KiB DC frame can't be
    # retransmitted on audio's 128 B pipe. Callers set this to the min
    # MTU across all candidate transports in the chain.
    mtu_override: int | None = None


OnRunnerEvent = Callable[[str, dict[str, object]], None]


class VpnRunner:
    def __init__(
        self,
        tun: TunDevice,
        transport: Transport,
        cfg: RunnerConfig | None = None,
    ) -> None:
        self._tun = tun
        self._transport = transport
        self._cfg = cfg or RunnerConfig()
        self._tunnel: Tunnel | None = None
        self._tun_rx: threading.Thread | None = None
        self._tx_worker: threading.Thread | None = None
        self._qos = PriorityQueue()
        self._stop = threading.Event()
        self._event_handlers: list[OnRunnerEvent] = []

    def start(self) -> None:
        if self._tunnel is not None:
            raise RuntimeError("runner already started")
        self._tunnel = Tunnel(
            self._transport,
            sess_id=self._cfg.sess_id,
            window=self._cfg.window,
            ack_timeout=self._cfg.ack_timeout,
            max_retries=self._cfg.max_retries,
            mtu_override=self._cfg.mtu_override,
        )
        self._tunnel.add_event_handler(self._emit)
        self._tunnel.start(on_packet=self._on_inbound)
        self._tun_rx = threading.Thread(
            target=self._tun_loop, name="vpn-tun-ingest", daemon=True
        )
        self._tun_rx.start()
        self._tx_worker = threading.Thread(
            target=self._qos_drain, name="vpn-qos-drain", daemon=True
        )
        self._tx_worker.start()
        log.info("vpn runner started: tun=%s sess=0x%x window=%d (qos=on)",
                 self._tun.name, self._cfg.sess_id, self._cfg.window)

    def add_event_handler(self, handler: OnRunnerEvent) -> None:
        self._event_handlers.append(handler)

    def stop(self) -> None:
        self._stop.set()
        self._qos.close()
        try:
            self._tun.close()  # unblocks os.read()
        except Exception:  # noqa: BLE001
            pass
        if self._tunnel is not None:
            self._tunnel.stop()
            self._tunnel = None
        if self._tun_rx is not None:
            self._tun_rx.join(timeout=2)
            self._tun_rx = None
        if self._tx_worker is not None:
            self._tx_worker.join(timeout=2)
            self._tx_worker = None

    def join(self) -> None:
        """Block until one of the threads exits (e.g. on transport close)."""
        if self._tun_rx is not None:
            self._tun_rx.join()

    @property
    def tunnel(self) -> Tunnel | None:
        return self._tunnel

    def swap_transport(self, transport: Transport) -> Transport:
        if self._tunnel is None:
            raise RuntimeError("runner not started")
        return self._tunnel.swap_transport(transport)

    # --- internals ---------------------------------------------------------

    def _tun_loop(self) -> None:
        """Read IP packets from the TUN device → QoS queue. Classification
        happens inside PriorityQueue.put(); this thread's only job is to
        keep the kernel's TUN buffer drained."""
        while not self._stop.is_set():
            try:
                pkt = self._tun.read_packet()
            except Exception:  # noqa: BLE001
                log.exception("tun read failed; stopping")
                return
            if pkt is None:
                return  # device closed
            if not pkt:
                continue
            self._qos.put(pkt)

    def _qos_drain(self) -> None:
        """Pop highest-priority packet → tunnel.send_packet. Runs in its
        own thread so the TUN reader never blocks on ARQ window."""
        while not self._stop.is_set():
            pkt = self._qos.get(timeout=0.25)
            if pkt is None:
                continue
            try:
                assert self._tunnel is not None
                self._tunnel.send_packet(pkt)
            except Exception:  # noqa: BLE001
                log.exception("tunnel send_packet failed")
                self._emit("runner_send_failed", {"error": "tunnel send_packet failed"})

    def _on_inbound(self, pkt: bytes) -> None:
        try:
            self._tun.write_packet(pkt)
        except OSError as e:
            log.warning("tun write failed: %s", e)

    def _emit(self, event: str, payload: dict[str, object]) -> None:
        for handler in list(self._event_handlers):
            try:
                handler(event, payload)
            except Exception:  # pragma: no cover
                log.exception("runner event handler raised")


def wait_for_signal() -> None:
    """Block until SIGINT/SIGTERM. Useful from CLI entry points."""
    import signal

    ev = threading.Event()

    def _handler(signum, frame):  # type: ignore[no-untyped-def]
        log.info("caught signal %d; shutting down", signum)
        ev.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handler)
        except ValueError:  # not main thread
            pass
    ev.wait()


def prompt_tun_setup_hint(name: str, addr: str, mtu: int) -> None:
    """Print a one-liner the user can copy-paste if TunDevice.open fails."""
    exe = os.path.realpath(os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "scripts", "vpn-setup-tun.sh"
    ))
    user = os.environ.get("USER", "$USER")
    print(
        f"\nTUN device {name!r} is not ready. Run this once as root:\n"
        f"  sudo {exe} {name} {addr} {mtu} {user}\n",
        flush=True,
    )
