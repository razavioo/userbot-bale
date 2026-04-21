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
    ('9120000000'). Drops leading 98 if present."""
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
        import os
        from playwright.async_api import async_playwright
        headless = os.environ.get("BALE_HEADLESS", "1") != "0"
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=headless)
        self._ctx = await self._browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="fa-IR",
        )
        self._page = await self._ctx.new_page()
        log.warning("Browser: launched (headless=%s)", headless)

    async def _dismiss_overlays(self) -> None:
        """Dismiss the PWA InstallGuide / privacy overlays that block clicks
        on 'ورود'. Target by aria-label for stability across RTL/LTR renders."""
        page = self._page
        for _ in range(20):
            for label in ("متوجه شدم", "باشه", "OK", "Accept"):
                try:
                    btn = page.locator(f"button[aria-label='{label}']").first
                    if await btn.count() > 0 and await btn.is_visible():
                        await btn.click()
                        log.warning("Browser: dismissed overlay '%s'", label)
                        await page.wait_for_timeout(800)
                        return
                except Exception:
                    pass
            await page.wait_for_timeout(500)
        log.warning("Browser: no dismiss-overlay button appeared")

    async def _async_start_phone_auth(self, phone_number: int) -> str:
        page = self._page
        phone_str = _iran_local(phone_number)

        log.warning("Browser: navigating to %s", _ORIGIN)
        try:
            await page.goto(_ORIGIN, wait_until="domcontentloaded", timeout=_TIMEOUT)
        except Exception:
            pass
        # The PWA InstallGuide overlay appears a few seconds after load.
        await page.wait_for_timeout(3000)

        await self._dismiss_overlays()

        # Click "ورود" (Login) to reveal the phone form. Match by
        # data-testid/aria-label so we don't hit any other element that
        # happens to contain the word.
        log.warning("Browser: clicking ورود")
        login_btn = page.locator(
            "button[data-testid='submit-button'][aria-label='ورود']"
        ).first
        await login_btn.wait_for(state="visible", timeout=_TIMEOUT)
        await login_btn.click(timeout=_TIMEOUT)
        await page.wait_for_timeout(1500)

        # Fill the phone input. The field's id contains Persian characters
        # and a space ("شماره همراه"), so use an attribute selector.
        # press_sequentially triggers real keystrokes so React sees every
        # keydown/input event and enables the submit button.
        log.warning("Browser: typing phone %s", phone_str)
        phone_input = page.locator("input[id='شماره همراه']").first
        await phone_input.click(timeout=_TIMEOUT)
        await phone_input.press_sequentially(phone_str, delay=40)
        await page.wait_for_timeout(500)

        # Click "تایید و ادامه" (Confirm and continue)
        log.warning("Browser: clicking تایید و ادامه (send SMS)")
        continue_btn = page.locator(
            "button[data-testid='submit-button']:not([disabled])"
        ).first
        await continue_btn.wait_for(state="visible", timeout=_TIMEOUT)
        await continue_btn.click(timeout=_TIMEOUT)

        # Wait for the SMS-code input to appear. Its id is "کد ورود"
        # (login code); placeholder is the 6-digit template "۱۲۳۴۵۶".
        log.warning("Browser: waiting for SMS-code input")
        await page.wait_for_selector(
            "input[id='کد ورود']", timeout=_TIMEOUT,
        )
        log.warning("Browser: SMS-code input visible")
        return "browser"

    async def _async_validate_code(self, code: str) -> str:
        page = self._page

        log.warning("Browser: typing code %s", code)
        code_input = page.locator("input[id='کد ورود']").first
        await code_input.click(timeout=_TIMEOUT)
        # Real keystrokes so React fires its onChange and re-enables the
        # submit button; .fill() set the DOM value directly but did not
        # trigger the keystroke events React relies on.
        await code_input.press_sequentially(code.strip(), delay=40)
        await page.wait_for_timeout(500)

        # Prefer clicking the (now-enabled) submit button. If something
        # keeps it disabled (e.g. 2FA prompt we don't know about), fall
        # back to pressing Enter which the form's onSubmit accepts.
        log.warning("Browser: submitting verify")
        # Try three ways in order: enabled-submit click → Enter key →
        # force-click on the (possibly disabled) submit button. Any of
        # them may have already triggered the submit; the cookie poll
        # below decides success.
        submitted = False
        try:
            verify_btn = page.locator(
                "button[data-testid='submit-button']:not([disabled])"
            ).first
            await verify_btn.wait_for(state="visible", timeout=5_000)
            await verify_btn.click(timeout=5_000)
            submitted = True
            log.warning("Browser: clicked (enabled) submit")
        except Exception:
            pass
        if not submitted:
            try:
                await code_input.press("Enter", timeout=3_000)
                submitted = True
                log.warning("Browser: submitted via Enter key")
            except Exception:
                pass
        if not submitted:
            try:
                await page.locator("button[data-testid='submit-button']").first.click(
                    force=True, timeout=3_000,
                )
                submitted = True
                log.warning("Browser: force-clicked submit")
            except Exception:
                pass

        # Poll for up to 20s: JWT cookie arrives OR the URL navigates
        # away from /login. Also bail early if the page shows an error.
        for i in range(20):
            await page.wait_for_timeout(1000)
            jwt = await self._async_get_jwt()
            if jwt:
                return jwt
            if "login" not in page.url:
                # Page navigated away — cookie should be there now.
                await page.wait_for_timeout(1000)
                jwt = await self._async_get_jwt()
                if jwt:
                    return jwt
                break

            # Look for an error toast/text on the page (e.g. wrong code)
            err_txt = await self._check_error_text()
            if err_txt:
                raise RuntimeError(f"Bale rejected: {err_txt}")

        # Diagnostic dump
        shot = f"/tmp/bale-verify-{int(__import__('time').time())}.png"
        try:
            await page.screenshot(path=shot)
        except Exception:
            pass
        cookies = [(c["name"], c.get("domain"), c.get("path")) for c in await self._ctx.cookies()]
        log.warning("Browser: no JWT yet. url=%s cookies=%s screenshot=%s",
                    page.url, cookies, shot)
        raise RuntimeError(
            f"Browser auth: no access_token cookie after verification. "
            f"Screenshot: {shot}"
        )

    async def _check_error_text(self) -> str | None:
        """Return any visible error message on the login page, or None."""
        page = self._page
        # Common error containers on the Bale login page
        selectors = [
            "[role='alert']",
            ".error, .errorMessage, .text-error",
            "span[class*='error' i], div[class*='error' i]",
        ]
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    txt = (await loc.inner_text()).strip()
                    if txt:
                        return txt
            except Exception:
                pass
        return None

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
