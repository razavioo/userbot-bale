from __future__ import annotations

import pytest

from userbot_bale.bale.protos import DialogInfo, SearchHit, SharedMediaHit
from userbot_bale.userbot.async_client import AsyncBaleClient, BaleClient
from userbot_bale.userbot.store import MemoryUserbotStore


class FakeApiClient:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.started = False
        self.messages_callback = None
        self.mark_read_calls: list[tuple[int, int]] = []
        self.dialogs: list[DialogInfo] = []
        self.search_hits: list[SearchHit] = []
        self.media_hits: list[SharedMediaHit] = []
        self.search_calls: list[dict] = []
        self.media_calls: list[dict] = []

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def listen_all_messages(self, callback) -> None:
        self.messages_callback = callback

    def send_message(
        self,
        peer_id: int,
        body: bytes,
        *,
        peer_type: int = 1,
        quoted_rid: int | None = None,
        is_silent: bool = False,
    ) -> None:
        self.sent.append({
            "peer_id": peer_id,
            "body": body,
            "text": body.decode("utf-8"),
            "peer_type": peer_type,
            "quoted_rid": quoted_rid,
            "is_silent": is_silent,
        })

    def mark_read(self, peer_id: int, date: int, *, peer_type: int = 1) -> None:
        self.mark_read_calls.append((peer_id, date))

    def load_dialogs(self, *, limit: int = 20):
        return self.dialogs[:limit]

    def load_history(self, peer_id: int, *, peer_type: int = 1, limit: int = 20):
        return []

    def search_messages_rpc(
        self,
        query: str,
        *,
        peer_id: int | None = None,
        peer_type: int | None = None,
        limit: int = 20,
    ):
        self.search_calls.append({
            "query": query,
            "peer_id": peer_id,
            "peer_type": peer_type,
            "limit": limit,
        })
        from userbot_bale.bale.protos import SearchMessagesPage
        return SearchMessagesPage(
            hits=self.search_hits[:limit],
            result_count=len(self.search_hits),
            load_more_state=b"",
        )

    def load_shared_media(
        self,
        peer_id: int,
        *,
        peer_type: int | None = None,
        date: int | None = None,
        content_type: int = 0,
        load_mode: int = 2,
        limit: int = 20,
    ):
        self.media_calls.append({
            "peer_id": peer_id,
            "peer_type": peer_type,
            "content_type": content_type,
            "limit": limit,
        })
        return self.media_hits[:limit]


JWT = "eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjo3N30.sig"


def _client(api: FakeApiClient | None = None) -> AsyncBaleClient:
    return BaleClient(
        jwt=JWT,
        api_client=api or FakeApiClient(),  # type: ignore[arg-type]
        store=MemoryUserbotStore(),
        allow_all=True,
    )


@pytest.mark.asyncio
async def test_search_messages_remote_hits_api():
    api = FakeApiClient()
    api.dialogs = [DialogInfo(peer_id=5, peer_type=1, unread_count=0, last_message_date=1)]
    api.search_hits = [
        SearchHit(
            peer_id=5, peer_type=1, sender_id=5, rid=11, text="hello invoice", date=100,
        ),
    ]
    client = _client(api)
    await client.start()
    try:
        results = await client.search_messages_remote("invoice", peer_id=5, limit=10)
        assert len(results) == 1
        assert results[0]["text"] == "hello invoice"
        assert results[0]["source"] == "remote_search"
        assert results[0]["peer_id"] == 5
        assert api.search_calls[0]["query"] == "invoice"
        assert api.search_calls[0]["peer_id"] == 5
        assert api.search_calls[0]["peer_type"] == 1
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_search_messages_remote_returns_empty_when_not_started():
    client = _client()
    assert await client.search_messages_remote("x") == []


@pytest.mark.asyncio
async def test_search_messages_remote_swallows_api_errors():
    api = FakeApiClient()

    def boom(*_args, **_kwargs):
        raise RuntimeError("server down")

    api.search_messages_rpc = boom  # type: ignore[method-assign]
    client = _client(api)
    await client.start()
    try:
        assert await client.search_messages_remote("x") == []
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_list_shared_media_hits_api():
    api = FakeApiClient()
    api.dialogs = [DialogInfo(peer_id=8, peer_type=1, unread_count=0, last_message_date=1)]
    api.media_hits = [
        SharedMediaHit(
            peer_id=8, peer_type=1, sender_id=8, rid=21, text="photo caption", date=200,
        ),
    ]
    client = _client(api)
    await client.start()
    try:
        media = await client.list_shared_media(8, limit=5, content_type=2)
        assert len(media) == 1
        assert media[0]["source"] == "shared_media"
        assert media[0]["peer_id"] == 8
        assert media[0]["text"] == "photo caption"
        assert api.media_calls[0]["peer_id"] == 8
        assert api.media_calls[0]["content_type"] == 2
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_list_shared_media_returns_empty_when_not_started():
    client = _client()
    assert await client.list_shared_media(1) == []


@pytest.mark.asyncio
async def test_list_rpc_paths_offline_inventory():
    payload = AsyncBaleClient.list_rpc_paths(service="auth.v1", limit=5)
    assert payload["count"] == 5
    assert all(p.startswith("/bale.auth.v1.Auth/") for p in payload["paths"])
    # BaleClient is an alias of AsyncBaleClient — offline path is shared.
    assert BaleClient.list_rpc_paths(limit=1)["count"] == 1


@pytest.mark.asyncio
async def test_search_messages_local_still_works():
    client = _client()
    # Memory store has search_messages
    results = await client.search_messages("anything")
    assert results == []
