"""Browser-driven Bale phone authentication.

This follows the live ``web.bale.ai`` login UI so the browser owns the
cookies, JavaScript session, and transport fingerprint. The selectors below
were checked against the current UI on 2026-08-17.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from pathlib import Path

log = logging.getLogger(__name__)

_ORIGIN = "https://web.bale.ai"
_TIMEOUT = 45_000  # ms; web.bale.ai can be slow.


def _new_loop() -> asyncio.AbstractEventLoop:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop


def _iran_local(phone_number: int) -> str:
    """Return the 10-digit local Iran number expected by the web form."""
    number = str(phone_number).lstrip("0")
    if number.startswith("98"):
        number = number[2:]
    return number


class BaleAuthBrowser:
    """Run one complete Bale web login and return its access-token cookie."""

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

    async def _async_start(self) -> None:
        import os

        from playwright.async_api import async_playwright

        headless = os.environ.get("BALE_HEADLESS", "1") != "0"
        self._pw = await async_playwright().start()
        try:
            self._browser = await self._pw.chromium.launch(headless=headless)
        except Exception as e:
            log.warning(
                "Browser: bundled Chromium unavailable (%s); "
                "falling back to system Chrome channel",
                e,
            )
            self._browser = await self._pw.chromium.launch(
                headless=headless,
                channel="chrome",
            )
        self._ctx = await self._browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="fa-IR",
        )
        self._page = await self._ctx.new_page()
        log.warning("Browser: launched (headless=%s)", headless)

    async def _dismiss_overlays(self) -> None:
        """Dismiss the PWA-install guide when it obscures the login button."""
        page = self._page
        for _ in range(20):
            for label in ("متوجه شدم", "باشه", "OK", "Accept"):
                try:
                    button = page.get_by_role("button", name=label, exact=True).first
                    if await button.count() > 0 and await button.is_visible():
                        await button.click()
                        log.warning("Browser: dismissed overlay %r", label)
                        await page.wait_for_timeout(800)
                        return
                except Exception:
                    pass
            await page.wait_for_timeout(500)
        log.warning("Browser: no dismissible overlay appeared")

    async def _async_start_phone_auth(self, phone_number: int) -> str:
        page = self._page
        phone = _iran_local(phone_number)

        log.warning("Browser: navigating to %s", _ORIGIN)
        try:
            await page.goto(_ORIGIN, wait_until="domcontentloaded", timeout=_TIMEOUT)
        except Exception:
            pass
        await page.wait_for_timeout(3000)
        await self._dismiss_overlays()

        log.warning("Browser: opening the phone form")
        login_button = page.get_by_role("button", name="ورود", exact=True).first
        await login_button.wait_for(state="visible", timeout=_TIMEOUT)
        await login_button.click(timeout=_TIMEOUT, force=True)
        await page.wait_for_timeout(1500)

        # This test-id hierarchy is exposed by the current React phone form.
        phone_input = page.locator(
            "[data-testid='phone-input'] [data-testid='textfield-single-line-input'], "
            "[data-testid='phone-input'] input"
        ).first
        await phone_input.wait_for(state="visible", timeout=_TIMEOUT)
        log.warning("Browser: entering phone %s", phone)
        await phone_input.fill(phone, timeout=_TIMEOUT)
        await page.wait_for_timeout(500)

        submit = page.locator("button[data-testid='submit-button']:not([disabled])").first
        await submit.wait_for(state="visible", timeout=_TIMEOUT)
        log.warning("Browser: requesting an SMS code")
        await submit.click(timeout=_TIMEOUT)

        code_input = page.get_by_role("textbox", name="کد ورود", exact=True).first
        await code_input.wait_for(state="visible", timeout=_TIMEOUT)
        log.warning("Browser: SMS-code input visible")
        return "browser"

    async def _async_validate_code(self, code: str) -> str:
        page = self._page
        code_input = page.get_by_role("textbox", name="کد ورود", exact=True).first
        await code_input.click(timeout=_TIMEOUT)
        # Keep real key events: the web app enables its submit button from
        # React input events, which is more reliable than setting the value.
        await code_input.press_sequentially(code.strip(), delay=40)
        await page.wait_for_timeout(500)

        log.warning("Browser: submitting SMS code")
        submitted = False
        try:
            submit = page.locator("button[data-testid='submit-button']:not([disabled])").first
            await submit.wait_for(state="visible", timeout=5_000)
            await submit.click(timeout=5_000)
            submitted = True
        except Exception:
            pass
        if not submitted:
            try:
                await code_input.press("Enter", timeout=3_000)
                submitted = True
            except Exception:
                pass
        if not submitted:
            try:
                await page.locator("button[data-testid='submit-button']").first.click(
                    force=True,
                    timeout=3_000,
                )
            except Exception:
                pass

        for _ in range(20):
            await page.wait_for_timeout(1000)
            jwt = await self._async_get_jwt()
            if jwt:
                return jwt
            jwt = await self._maybe_complete_signup_profile()
            if jwt:
                return jwt
            if "login" not in page.url:
                await page.wait_for_timeout(1000)
                jwt = await self._async_get_jwt()
                if jwt:
                    return jwt
                break
            error_text = await self._check_error_text()
            if error_text:
                raise RuntimeError(f"Bale rejected: {error_text}")

        jwt = await self._maybe_complete_signup_profile()
        if jwt:
            return jwt

        screenshot = Path(tempfile.gettempdir()) / f"bale-verify-{int(time.time())}.png"
        try:
            await page.screenshot(path=str(screenshot))
        except Exception:
            pass
        cookies = [
            (cookie["name"], cookie.get("domain"), cookie.get("path"))
            for cookie in await self._ctx.cookies()
        ]
        log.warning(
            "Browser: no JWT yet. url=%s cookies=%s screenshot=%s",
            page.url,
            cookies,
            screenshot,
        )
        raise RuntimeError(
            "Browser auth: no access_token cookie after verification. "
            f"Screenshot: {screenshot}"
        )

    async def _maybe_complete_signup_profile(self) -> str | None:
        """Complete the optional profile-name step for a fresh phone number."""
        import os

        page = self._page
        try:
            name_input = page.get_by_role("textbox", name="نام", exact=True).first
            if await name_input.count() == 0 or not await name_input.is_visible():
                return None
            display_name = os.environ.get("BALE_SIGNUP_NAME", "Baleobala")
            log.warning("Browser: completing signup profile name=%s", display_name)
            await name_input.fill(display_name, timeout=5_000)
            await page.wait_for_timeout(500)
            submit = page.locator("button[data-testid='submit-button']:not([disabled])").first
            await submit.wait_for(state="visible", timeout=10_000)
            await submit.click(timeout=10_000, force=True)
            for _ in range(25):
                await page.wait_for_timeout(1000)
                jwt = await self._async_get_jwt()
                if jwt:
                    return jwt
                if "login" not in page.url:
                    jwt = await self._async_get_jwt()
                    if jwt:
                        return jwt
            return None
        except Exception as e:
            log.warning("Browser: signup profile completion failed: %s", e)
            return None

    async def _check_error_text(self) -> str | None:
        page = self._page
        for selector in (
            "[role='alert']",
            ".error, .errorMessage, .text-error",
            "span[class*='error' i], div[class*='error' i]",
        ):
            try:
                element = page.locator(selector).first
                if await element.count() > 0 and await element.is_visible():
                    text = (await element.inner_text()).strip()
                    if text:
                        return text
            except Exception:
                pass
        return None

    async def _async_get_jwt(self) -> str | None:
        for cookie in await self._ctx.cookies():
            if cookie["name"] == "access_token" and cookie.get("value"):
                return cookie["value"]
        return None

    async def _async_close(self) -> None:
        try:
            if self._browser:
                await self._browser.close()
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass

    def start_phone_auth(self, phone_number: int) -> str:
        return self._loop.run_until_complete(self._async_start_phone_auth(phone_number))

    def validate_code(self, code: str, **_) -> "AuthSession":
        from baleobala.bale.auth import AuthSession

        jwt = self._loop.run_until_complete(self._async_validate_code(code))
        log.warning("Browser: JWT obtained, length=%d", len(jwt))
        return AuthSession(jwt=jwt, response_body=b"")

    def current_jwt(self) -> str | None:
        return self._loop.run_until_complete(self._async_get_jwt())

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
