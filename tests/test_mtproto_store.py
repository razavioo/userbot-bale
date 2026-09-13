from __future__ import annotations

from userbot_bale.bale.endpoints import Endpoint
from userbot_bale.bale.mtproto import (
    MtprotoAuthKey,
    MtprotoSession,
    MtprotoSessionState,
    MtprotoSessionStore,
    PersistedMtprotoSession,
)


class _Rpc:
    def __init__(self) -> None:
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def close(self) -> None:
        self.closed = True


def test_mtproto_session_store_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("USERBOT_BALE_HOME", str(tmp_path))
    store = MtprotoSessionStore()
    session = PersistedMtprotoSession(
        endpoint_host="rpc-c002.bale.ai",
        endpoint_port=443,
        endpoint_scheme="tls",
        endpoint_pin="a" * 64,
        auth_key_id="kid-1",
        auth_key_hex="cafe",
        session_id="sess-1",
        created_at=1.0,
        updated_at=2.0,
    )
    store.save(session)
    loaded = store.load()
    assert loaded == session
    assert loaded is not None
    assert loaded.auth_key().key_hex == "cafe"


def test_mtproto_session_persists_on_start(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("USERBOT_BALE_HOME", str(tmp_path))
    store = MtprotoSessionStore()
    state = MtprotoSessionState.create(
        endpoint=Endpoint(
            scheme="tls",
            pin="a" * 64,
            host="rpc-c002.bale.ai",
            ip="1.2.3.4",
            port=443,
            id=1013,
        ),
        auth_key=MtprotoAuthKey(key_id="kid-1", key_hex="cafe"),
        session_id="sess-1",
    )
    rpc = _Rpc()
    session = MtprotoSession(state=state, rpc_client=rpc, store=store)
    session.start()
    loaded = store.load()
    assert rpc.started is True
    assert loaded is not None
    assert loaded.session_id == "sess-1"
    session.close()
    assert rpc.closed is True
