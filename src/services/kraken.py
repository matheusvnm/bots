"""
Kraken bot — dev cookie bypass with first-login fallback.

First login (no state.json):
  - Normal login: credentials + OTP
  - Kraken triggers /device-approval → user approves via email
  - On success the storage state is saved → future runs skip device-approval

Subsequent logins (state.json present):
  - Injects `dev` cookie from saved state before login
  - Kraken recognises the device → skips /device-approval entirely
"""

import json
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from loguru import logger
from patchright.sync_api import BrowserContext, Page, sync_playwright

from components.dtos import KrakenCredentials
from components.network import attach_network_logger
from components.trace import ScreenshotTracer, TraceContext
from components.utils import wait_for_url


class KrakenPages:
    LOGIN = "https://id.kraken.com/sign-in"
    DASHBOARD = "https://www.kraken.com/c"
    DEVICE_APPROVAL = "https://id.kraken.com/device-approval"
    PASSKEY = "https://id.kraken.com/enable-passkey"


class KrakenBot:

    def __init__(self, tracer: ScreenshotTracer, **_: dict[str, Any]):
        self.tracer = tracer

    def get_dev_cookie(self, state_file_path: str) -> dict:
        path = Path(state_file_path)
        state = json.loads(path.read_text())
        cookie = next((c for c in state["cookies"] if c["name"] == "dev"), None)
        if not cookie:
            logger.error("`dev` cookie not found in {}", path)
            raise ValueError("`dev` cookie not found in storage_state")
        return cookie

    @contextmanager
    def with_browser(self, state_file_path: str | None = None):
        with sync_playwright() as p:
            browser = p.chromium.launch(
                channel="chrome",
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                    "--no-sandbox",
                    "--window-size=1440,900",
                ],
            )
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/145.0.0.0 Safari/537.36"
                ),
                locale="en-US",
                timezone_id="America/New_York",
            )

            if state_file_path is not None and Path(state_file_path).exists():
                dev_cookie = self.get_dev_cookie(state_file_path)
                context.add_cookies([dev_cookie])

            try:
                yield context
            finally:
                browser.close()

    def _attach_page_hooks(self, page: Page) -> None:
        """Wire network logging and auto-screenshot on every main frame navigation."""
        attach_network_logger(page)

        def on_navigated(frame) -> None:
            if frame.parent_frame is not None:
                return

            logger.debug("[NAV] {}", frame.url)
            try:
                parsed = urlparse(frame.url)
                slug = re.sub(r"[^\w]+", "_", f"{parsed.netloc}{parsed.path}").strip("_")[:60]
                self.tracer.screenshot(page, f"nav_{slug}")
            except Exception:
                pass

        page.on("framenavigated", on_navigated)

    # ── OTP handler ────────────────────────────────────────────────────────

    def _handle_otp(
        self,
        page: Page,
        expected_urls: list[str],
        max_retries: int = 3,
    ) -> None:
        """
        Prompt for OTP, submit, and retry if Kraken rejects the code.

        Rejection is detected by the tfa input becoming visible again after
        a short processing window — meaning the form is still on screen.
        Navigation to any of expected_urls means the OTP was accepted.
        """
        def already_navigated() -> bool:
            return any(page.url.startswith(u) for u in expected_urls)

        for attempt in range(1, max_retries + 1):
            # A previous OTP may have been accepted with a delay — check before doing anything
            if already_navigated():
                logger.info("Navigation detected before attempt {} — OTP accepted", attempt)
                return

            self.tracer.screenshot(page, f"otp_{attempt:02d}_prompt")
            otp = input(f"[?] OTP code (attempt {attempt}/{max_retries}): ").strip()

            # Page may have navigated while the user was typing
            if already_navigated():
                logger.info("Navigation detected while typing — OTP accepted")
                return

            try:
                page.fill('input[name="tfa"]', otp)
                page.keyboard.press("Enter")
            except Exception:
                # tfa input gone — the page navigated away (delayed from a previous attempt)
                if already_navigated():
                    logger.info("tfa input gone, page already navigated — OTP accepted")
                    return
                raise

            self.tracer.screenshot(page, f"otp_{attempt:02d}_submitted")
            logger.info("OTP submitted (attempt {})", attempt)

            waited = 0
            interval = 1000
            otp_rejected = False

            while waited < 30000:
                page.wait_for_timeout(interval)
                waited += interval

                if already_navigated():
                    return  # accepted

                # Wait at least 6 s before concluding rejection — Kraken can be slow
                if waited >= 6000 and page.locator('input[name="tfa"]').is_visible():
                    otp_rejected = True
                    break

            if otp_rejected:
                self.tracer.screenshot(page, f"otp_{attempt:02d}_rejected")
                logger.error("OTP rejected — code incorrect")
                if attempt == max_retries:
                    raise RuntimeError(f"OTP failed after {max_retries} attempts")
                continue

            self.tracer.screenshot(page, f"otp_{attempt:02d}_timeout")
            raise TimeoutError(f"Post-OTP navigation timeout (url={page.url})")

        raise RuntimeError(f"OTP failed after {max_retries} attempts")

    def _first_login(self, context: BrowserContext, credentials: KrakenCredentials) -> None:
        """Normal login — device-approval expected. Saves state on success."""
        logger.info("First login — no state.json found")
        logger.info("Device approval will be triggered: check your email and click the link")

        page = context.new_page()
        self._attach_page_hooks(page)

        page.goto(KrakenPages.LOGIN)
        page.fill('input[name="username"]', credentials.email)
        page.fill('input[name="password"]', credentials.password)
        page.click('button[type="submit"]')
        self.tracer.screenshot(page, "credentials_submitted")

        logger.info("Waiting for OTP field...")
        page.wait_for_selector('input[name="tfa"]', timeout=60000)

        all_pages = [KrakenPages.DASHBOARD, KrakenPages.DEVICE_APPROVAL, KrakenPages.PASSKEY]
        self._handle_otp(page, expected_urls=all_pages)

        if page.url.startswith(KrakenPages.DEVICE_APPROVAL):
            logger.info("Device approval page reached — waiting for email confirmation (up to 5 min)...")
            if not wait_for_url(page, [KrakenPages.DASHBOARD, KrakenPages.PASSKEY], timeout=300000):
                self.tracer.screenshot(page, "fail_device_approval_timeout")
                raise TimeoutError("Device approval timeout — link not clicked within 5 minutes")

        if page.url.startswith(KrakenPages.PASSKEY):
            try:
                page.click('text="Maybe later"')
                self.tracer.screenshot(page, "passkey_maybe_later_clicked")
                wait_for_url(page, [KrakenPages.DASHBOARD], timeout=10000)
                self.tracer.screenshot(page, "passkey_dismissed")
            except Exception:
                pass

        logger.info("First login successful — saving state for future runs")
        state_path = Path(credentials.device_cookie_path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(state_path))
        logger.info("State saved → {}", state_path)
        self.tracer.screenshot(page, "pass_first_login")

    def _bypass_login(self, context: BrowserContext, credentials: KrakenCredentials) -> None:
        """Login with injected `dev` cookie — device-approval should not trigger."""
        logger.info("State found — using `dev` cookie bypass")

        page = context.new_page()
        self._attach_page_hooks(page,)

        page.goto(KrakenPages.LOGIN)
        page.fill('input[name="username"]', credentials.email)
        page.fill('input[name="password"]', credentials.password)
        page.click('button[type="submit"]')
        self.tracer.screenshot(page, "credentials_submitted")

        logger.info("Waiting for OTP field...")
        page.wait_for_selector('input[name="tfa"]', timeout=60000)

        expected = [KrakenPages.DASHBOARD, KrakenPages.DEVICE_APPROVAL, KrakenPages.PASSKEY]
        self._handle_otp(page, expected_urls=expected)

        if page.url.startswith(KrakenPages.DEVICE_APPROVAL):
            self.tracer.screenshot(page, "fail_device_approval")
            raise RuntimeError("Device-approval triggered — `dev` cookie is NOT sufficient.")

        if page.url.startswith(KrakenPages.PASSKEY):
            try:
                page.click('text="Maybe later"')
                self.tracer.screenshot(page, "passkey_maybe_later_clicked")
                wait_for_url(page, [KrakenPages.DASHBOARD], timeout=10000)
                self.tracer.screenshot(page, "passkey_dismissed")
            except Exception:
                pass

        logger.info("PASS — dashboard reached. `dev` cookie IS the device trust mechanism.")
        self.tracer.screenshot(page, "pass_dashboard")

    def run(self, credentials: KrakenCredentials) -> None:
        state_path = Path(credentials.device_cookie_path)
        is_first_login = not state_path.exists()

        cookie_path = None if is_first_login else credentials.device_cookie_path

        with self.with_browser(cookie_path) as context:
            if is_first_login:
                self._first_login(context, credentials)
                return
            
            self._bypass_login(context, credentials)
