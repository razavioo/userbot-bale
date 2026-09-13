from __future__ import annotations

from userbot_bale.vpn.keepalive import LiveKitKeepalive


class _TerminalSession:
    def __init__(self) -> None:
        self.submits = 0

    def is_terminal(self) -> bool:
        return True

    def _submit_data(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        self.submits += 1


def test_keepalive_exits_without_publishing_after_session_terminal() -> None:
    session = _TerminalSession()
    keepalive = LiveKitKeepalive(session, interval=0.01)
    keepalive.start()
    keepalive.stop()
    assert session.submits == 0
