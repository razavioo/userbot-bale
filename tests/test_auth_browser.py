from __future__ import annotations

import asyncio

from userbot_bale.bale.auth_browser import BaleAuthBrowser, _iran_local


def test_iran_local_normalizes_international_and_local_numbers() -> None:
    assert _iran_local(989120000000) == "9120000000"
    assert _iran_local(9120000000) == "9120000000"


def test_browser_auth_accepts_a_session_completed_in_visible_browser() -> None:
    class FakeContext:
        async def cookies(self):
            return [{"name": "access_token", "value": "test-jwt"}]

    auth = object.__new__(BaleAuthBrowser)
    auth._ctx = FakeContext()
    assert asyncio.run(auth._async_validate_code("")) == "test-jwt"
