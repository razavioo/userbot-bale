"""
Playwright-based Bale auth — drives the real web.bale.ai UI in headless
Chromium so every cookie, JS session, and TLS/HTTP2 fingerprint is
authentic. Uses the async Playwright API with an explicit new event
loop so it runs cleanly inside QThread workers.

UI flow on web.bale.ai (2026-04-21):
    1. goto https://web.bale.ai → lands on /login
    2. click "متوجه شدم" (dismiss privacy dialog, if shown)
    3. click "ورود" (reveal phone form)
    4. fill phone input (id = "شماره همراه"), 10-digit local Iran format
    5. click "تایید و ادامه" → SMS sent
    6. fill code input (maxlength=5 or 6)
    7. click "تایید و ادامه" again → page navigates on success
    8. read `access_token` cookie from the browser context
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

_ORIGIN = "https://web.bale.ai"
_TIMEOUT = 45_000  # ms; web.bale.ai is slow sometimes


def _new_loop() -> asyncio.AbstractEventLoop:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop


def _iran_local(phone_number: int) -> str:
    """Convert any international Iran phone (with/without country code)
    to the 10-digit local format the UI's phone field expects
    ('9127479731'). Drops leading 98 if present."""
    s = str(phone_number).lstrip("0")
    if s.startswith("98"):
        s = s[2:]
    return s


class BaleAuthBrowser:
    """Phone-auth flow driven through a headless Chromium page.

    Call close() when done (or use as a context manager).
    Public methods are synchronous; they run their async impl on a
    dedicated event loop owned by this instance."""

    def __init__(self) -> None:
        try:
            import playwright  # noqa: F401
        except ImportError as e:
            raise ImportError(
                "playwright required: pip install playwright && "
                "playwright install chromium"
            ) from e
        self._loop = _new_loop()
        self._pw = None
        self._browser = None
        self._ctx = None
        self._page = None
        self._loop.run_until_complete(self._async_start())

    # ------------------------------------------------------------------
    # Async internals
    # ------------------------------------------------------------------

    async def _async_start(self) -> None:
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True)
        self._ctx = await self._browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="fa-IR",
        )
        self._page = await self._ctx.new_page()

    async def _dismiss_overlays(self) -> None:
        """Dismiss any privacy/terms dialogs that may block the form."""
        for label in ("متوجه شدم", "باشه", "OK", "Accept"):
            try:
                btn = self._page.locator(f"button:has-text('{label}')").first
                if await btn.is_visible(timeout=1_000):
                    await btn.click()
                    await self._page.wait_for_timeout(500)
            except Exception:
                pass

    async def _async_start_phone_auth(self, phone_number: int) -> str:
        page = self._page
        phone_str = _iran_local(phone_number)

        log.warning("Browser: navigating to %s", _ORIGIN)
        try:
            await page.goto(_ORIGIN, wait_until="domcontentloaded", timeout=_TIMEOUT)
        except Exception:
            # Slow network — still try to continue; page might be partial
            pass
        await page.wait_for_timeout(2000)

        await self._dismiss_overlays()

        # Click "ورود" (Login) to reveal the phone form
        log.warning("Browser: clicking ورود")
        login_btn = page.locator("button:has-text('ورود')").first
        await login_btn.click(timeout=_TIMEOUT)
        await page.wait_for_timeout(1500)

        # Fill the phone input. The field's id contains Persian characters
        # and a space ("شماره همراه"), so use an attribute selector.
        log.warning("Browser: filling phone %s", phone_str)
        phone_input = page.locator("input[id='شماره همراه']").first
        await phone_input.fill(phone_str, timeout=_TIMEOUT)
        await page.wait_for_timeout(500)

        # Click "تایید و ادامه" (Confirm and continue)
        log.warning("Browser: clicking تایید و ادامه (send SMS)")
        continue_btn = page.locator("button:has-text('تایید و ادامه')").first
        await continue_btn.click(timeout=_TIMEOUT)

        # Wait for the SMS-code input to appear. The phone input disappears;
        # a shorter input (maxlength 5-6) takes its place.
        log.warning("Browser: waiting for SMS-code input")
        await page.wait_for_timeout(2000)
        # The code input is the first visible text/number input that isn't
        # the country or phone field we already saw.
        await page.wait_for_selector(
            "input[maxlength='5'], input[maxlength='6'], "
            "input[type='number'], input[inputmode='numeric']",
            timeout=_TIMEOUT,
        )
        log.warning("Browser: SMS-code input visible")
        return "browser"

    async def _async_validate_code(self, code: str) -> str:
        page = self._page

        log.warning("Browser: filling code %s", code)
        code_input = page.locator(
            "input[maxlength='5'], input[maxlength='6'], "
            "input[type='number'], input[inputmode='numeric']"
        ).first
        await code_input.fill(code.strip(), timeout=_TIMEOUT)
        await page.wait_for_timeout(500)

        log.warning("Browser: clicking تایید و ادامه (verify)")
        verify_btn = page.locator(
            "button:has-text('تایید و ادامه'), button:has-text('تایید'), "
            "button:has-text('ادامه')"
        ).first
        await verify_btn.click(timeout=_TIMEOUT)

        # Wait for auth to complete: either the URL changes away from
        # /login, or the access_token cookie gets set.
        for _ in range(30):  # ~30s total
            await page.wait_for_timeout(1000)
            jwt = await self._async_get_jwt()
            if jwt:
                return jwt
            if "login" not in page.url:
                # On dashboard; JWT cookie is there
                jwt = await self._async_get_jwt()
                if jwt:
                    return jwt

        cookies = [c["name"] for c in await self._ctx.cookies()]
        log.warning("Browser: page URL=%s cookies=%s", page.url, cookies)
        raise RuntimeError(
            "Browser auth: no access_token cookie after verification. "
            "Code may be wrong, or the page hasn't navigated yet."
        )

    async def _async_get_jwt(self) -> str | None:
        for c in await self._ctx.cookies():
            if c["name"] == "access_token" and c.get("value"):
                return c["value"]
        return None

    async def _async_close(self) -> None:
        try:
            if self._browser:
                await self._browser.close()
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Public synchronous interface
    # ------------------------------------------------------------------

    def start_phone_auth(self, phone_number: int) -> str:
        return self._loop.run_until_complete(
            self._async_start_phone_auth(phone_number)
        )

    def validate_code(self, code: str, **_) -> "AuthSession":
        from baleobala.bale.auth import AuthSession
        jwt = self._loop.run_until_complete(self._async_validate_code(code))
        log.warning("Browser: JWT obtained, length=%d", len(jwt))
        return AuthSession(jwt=jwt, response_body=b"")

    def close(self) -> None:
        try:
            self._loop.run_until_complete(self._async_close())
        except Exception:
            pass
        try:
            self._loop.close()
        except Exception:
            pass

    def __enter__(self) -> "BaleAuthBrowser":
        return self

    def __exit__(self, *_) -> None:
        self.close()
