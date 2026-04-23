from __future__ import annotations

import threading

from baleobala.bale.protos import IncomingCallEvent
from baleobala.carrier.bale import BaleCarrierController


class _FakeCreds:
    url = "wss://example.test"
    token = "token"
    room = "room"


class _FakeClient:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.callback = None

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def listen_incoming_calls(self, callback) -> None:  # noqa: ANN001
        self.callback = callback


def test_answer_sets_ready_event_after_listener_is_registered() -> None:
    client = _FakeClient()
    controller = BaleCarrierController(client=client)
    ready = threading.Event()
    result_holder = {}

    def run_answer() -> None:
        result_holder["creds"] = controller.answer(timeout=1.0, ready_event=ready)

    t = threading.Thread(target=run_answer, daemon=True)
    t.start()

    assert ready.wait(timeout=0.5)
    assert client.started is True
    assert client.callback is not None

    client.callback(IncomingCallEvent(credentials=_FakeCreds(), peer_id=7))
    t.join(timeout=1.0)

    creds = result_holder["creds"]
    assert creds.url == "wss://example.test"
    assert creds.token == "token"
    assert creds.room == "room"
    assert client.stopped is True
