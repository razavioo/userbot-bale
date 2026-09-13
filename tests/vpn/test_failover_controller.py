from __future__ import annotations

import queue
import time

from userbot_bale.vpn.router import FailoverController, TransportPool, RouterChoice
from userbot_bale.vpn.transports import InMemoryTransport
from userbot_bale.vpn.tunnel import Tunnel


class _DummyRunner:
    def __init__(self, tunnel: Tunnel) -> None:
        self._tunnel = tunnel
        self._handlers = []

    @property
    def tunnel(self) -> Tunnel:
        return self._tunnel

    def add_event_handler(self, handler) -> None:  # noqa: ANN001
        self._handlers.append(handler)

    def swap_transport(self, transport):  # noqa: ANN001
        return self._tunnel.swap_transport(transport)


class _DummySession:
    def __init__(self) -> None:
        self._observers = []
        self._terminal = False
        self.terminal_error = None

    def add_terminal_observer(self, observer) -> None:  # noqa: ANN001
        self._observers.append(observer)

    def is_terminal(self) -> bool:
        return self._terminal

    def stop(self) -> None:
        self._terminal = True


class _FakeChannel:
    mtu = 16 * 1024
    rate_hint = 1e6

    def __init__(self, session, topic: str) -> None:  # noqa: ANN001
        self._session = session
        self._topic = topic
        self._closed = False

    def send_bytes(self, data: bytes) -> None:
        if self._closed:
            return
        assert self._session.peer is not None
        q = self._session.peer._queues.setdefault(self._topic, queue.Queue())
        q.put(bytes(data))

    def recv_bytes(self, timeout: float | None = None) -> bytes | None:
        q = self._session._queues.setdefault(self._topic, queue.Queue())
        try:
            return q.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self._closed = True


class _CoordSession(_DummySession):
    def __init__(self) -> None:
        super().__init__()
        self._queues: dict[str, "queue.Queue[bytes]"] = {}
        self.peer: "_CoordSession | None" = None

    def data_channel(self, topic: str = "vpn", *, reliable: bool = True):  # noqa: ARG002
        return _FakeChannel(self, topic)


def _pair_coord_sessions() -> tuple[_CoordSession, _CoordSession]:
    a = _CoordSession()
    b = _CoordSession()
    a.peer = b
    b.peer = a
    return a, b


def test_transport_pool_reuses_preferred_candidate_after_cooldown():
    calls: list[str] = []

    def a():
        calls.append("a")
        return object()

    failures = {"b": 1}

    def b():
        calls.append("b")
        if failures["b"] > 0:
            failures["b"] -= 1
            raise RuntimeError("nope")
        return object()

    pool = TransportPool(
        [RouterChoice("b", b), RouterChoice("a", a)],
        cooldown_seconds=0.0,
    )
    name, _transport = pool.start()
    assert name == "a"
    name2, _transport2 = pool.advance()
    assert name2 == "b"
    assert calls == ["b", "a", "b"]


def test_failover_controller_swaps_transport_and_preserves_tunnel():
    a_old, b_side = InMemoryTransport.pair(mtu=200)
    a_new, b_side_new = InMemoryTransport.pair(mtu=200)

    class _Merged:
        mtu = 200
        rate_hint = 1e6

        def __init__(self, left, right):
            self._left = left
            self._right = right

        def send_bytes(self, data: bytes) -> None:
            self._left.send_bytes(data)
            self._right.send_bytes(data)

        def recv_bytes(self, timeout: float | None = None) -> bytes | None:
            buf = self._left.recv_bytes(timeout=(timeout or 0.1) / 2)
            if buf is not None:
                return buf
            return self._right.recv_bytes(timeout=(timeout or 0.1) / 2)

        def close(self) -> None:
            return None

    merged_b = _Merged(b_side, b_side_new)
    a = Tunnel(a_old, sess_id=0x42, ack_timeout=0.05, mtu_override=200, window=4)
    b = Tunnel(merged_b, sess_id=0x42, ack_timeout=0.05, mtu_override=200, window=4)
    rx: "queue.Queue[bytes]" = queue.Queue()
    try:
        b.start(on_packet=rx.put)
        a.start(on_packet=lambda _: None)
        runner = _DummyRunner(a)
        pool = TransportPool([RouterChoice("audio", lambda: a_new)], cooldown_seconds=0.0)
        pool._current = a_old  # type: ignore[attr-defined]
        pool._current_name = "dc"  # type: ignore[attr-defined]
        controller = FailoverController(runner, pool)
        controller.start("dc")

        a.send_packet(b"before")
        assert rx.get(timeout=2) == b"before"

        assert controller.trigger_failover("test")
        time.sleep(0.1)
        a.send_packet(b"after")
        assert rx.get(timeout=2) == b"after"
        assert controller.status["transport_selected"] == "audio"
        assert controller.status["transport_previous"] == "dc"
        assert controller.status["recovery_state"] == "healthy"
    finally:
        a.stop()
        b.stop()
        a_old.close()
        b_side.close()
        a_new.close()
        b_side_new.close()


def test_failover_controller_rebuilds_carrier_when_pool_exhausted():
    a_old, b_old = InMemoryTransport.pair(mtu=200)
    a_new, b_new = InMemoryTransport.pair(mtu=200)

    client = Tunnel(a_old, sess_id=7, ack_timeout=0.05, mtu_override=200)
    server = Tunnel(b_new, sess_id=7, ack_timeout=0.05, mtu_override=200)
    rx: "queue.Queue[bytes]" = queue.Queue()
    try:
        client.start(on_packet=lambda _: None)
        server.start(on_packet=rx.put)
        runner = _DummyRunner(client)
        dead_pool = TransportPool([RouterChoice("dead", lambda: (_ for _ in ()).throw(RuntimeError("dead")))], cooldown_seconds=0.0)
        dead_pool._current = a_old  # type: ignore[attr-defined]
        dead_pool._current_name = "dc"  # type: ignore[attr-defined]

        def rebuild():
            session = _DummySession()
            pool = TransportPool([RouterChoice("audio", lambda: a_new)], cooldown_seconds=0.0)
            return session, pool

        controller = FailoverController(runner, dead_pool, carrier_session=_DummySession(), carrier_factory=rebuild)
        controller.start("dc")
        assert controller.trigger_failover("carrier_terminal")
        time.sleep(0.1)
        client.send_packet(b"recovered")
        assert rx.get(timeout=2) == b"recovered"
        assert controller.status["transport_selected"] == "audio"
        assert controller.status["recovery_state"] == "healthy"
        assert controller.status["call_established"] == "yes"
    finally:
        client.stop()
        server.stop()
        a_old.close()
        b_old.close()
        a_new.close()
        b_new.close()


def test_failover_controller_commits_prepared_peer_transport():
    a_old, b_old = InMemoryTransport.pair(mtu=200)
    a_new, b_new = InMemoryTransport.pair(mtu=200)
    sess_a, sess_b = _pair_coord_sessions()

    client = Tunnel(a_old, sess_id=9, ack_timeout=0.05, mtu_override=200, window=4)
    server = Tunnel(b_old, sess_id=9, ack_timeout=0.05, mtu_override=200, window=4)
    rx: "queue.Queue[bytes]" = queue.Queue()
    try:
        client.start(on_packet=lambda _: None)
        server.start(on_packet=rx.put)

        controller_a = FailoverController(
            _DummyRunner(client),
            TransportPool([RouterChoice("audio", lambda: a_new)], cooldown_seconds=0.0),
            carrier_session=sess_a,
        )
        controller_b = FailoverController(
            _DummyRunner(server),
            TransportPool([RouterChoice("audio", lambda: b_new)], cooldown_seconds=0.0),
            carrier_session=sess_b,
        )
        controller_a.start("dc")
        controller_b.start("dc")

        assert controller_a.trigger_failover("test-coordination")
        time.sleep(0.3)
        client.send_packet(b"after-commit")
        assert rx.get(timeout=2) == b"after-commit"
        assert controller_b.status["transport_selected"] == "audio"
        assert controller_b.status["peer_coordination"] == "active"
    finally:
        client.stop()
        server.stop()
        a_old.close()
        b_old.close()
        a_new.close()
        b_new.close()
