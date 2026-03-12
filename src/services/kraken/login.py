import re
from contextlib import contextmanager
from pathlib import Path
from typing import Generator
from urllib.parse import urlparse

from services.kraken.interceptors import KrakenInterceptor
from services.kraken.exceptions import NoOTPAuthenticatorError
from loguru import logger
from patchright.sync_api import Page, sync_playwright

from components.dtos import KrakenCredentials
from components.network import attach_network_logger
from components.trace import PageTracer
from components.utils import wait_for_url

from .constants import KrakenPages


class KrakenAuthenticator:
    def __init__(self, tracer: PageTracer):
        self.tracer = tracer

    @contextmanager
    def with_browser(self, state_file_path: Path):
        """
        Launch a browser context.

        If state_file_path exists the full session state is restored.
        Yields (context, first_login) where first_login is True when no state file existed.
        Saves storage state back to disk on clean exit.
        """
        first_login = not state_file_path.exists()
        if first_login:
            logger.info(
                "No existing state file at {} — starting with fresh context",
                state_file_path,
            )
            state_file_path.parent.mkdir(parents=True, exist_ok=True)

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
                storage_state=str(state_file_path),
            )
            try:
                yield context, first_login
            finally:
                context.storage_state(path=str(state_file_path))
                logger.info("Saved storage state → {}", state_file_path)
                browser.close()

    def _attach_page_hooks(self, page: Page) -> None:
        """Wire network logging and auto-save on every main frame navigation."""
        attach_network_logger(page)

        def on_navigated(frame) -> None:
            if frame.parent_frame is not None:
                return
            logger.debug("[NAV] {}", frame.url)
            try:
                parsed = urlparse(frame.url)
                slug = re.sub(r"[^\w]+", "_", f"{parsed.netloc}{parsed.path}").strip(
                    "_"
                )[:60]
                self.tracer.save(page, f"nav_{slug}")
            except Exception:
                pass

        page.on("framenavigated", on_navigated)

    def _handle_otp(
        self, page: Page, expected_urls: list[str], max_retries: int = 3
    ) -> None:
        """
        Prompt for OTP, submit, and retry if Kraken rejects the code.

        Rejection is detected by the tfa input becoming visible again after
        a short processing window. Navigation to any expected URL means accepted.
        """

        def already_navigated() -> bool:
            return any(page.url.startswith(u) for u in expected_urls)

        for attempt in range(1, max_retries + 1):
            if already_navigated():
                logger.info(
                    "Navigation detected before attempt {} — OTP accepted", attempt
                )
                return

            self.tracer.save(page, f"otp_{attempt:02d}_prompt")
            otp = input(f"[?] OTP code (attempt {attempt}/{max_retries}): ").strip()

            if already_navigated():
                logger.info("Navigation detected while typing — OTP accepted")
                return

            try:
                page.fill('input[name="tfa"]', otp)
                page.keyboard.press("Enter")
            except Exception:
                if already_navigated():
                    logger.info("tfa input gone, page already navigated — OTP accepted")
                    return
                raise

            self.tracer.save(page, f"otp_{attempt:02d}_submitted")
            logger.info("OTP submitted (attempt {})", attempt)

            waited = 0
            interval = 1000
            otp_rejected = False

            while waited < 30000:
                page.wait_for_timeout(interval)
                waited += interval

                if already_navigated():
                    return

                if waited >= 6000 and page.locator('input[name="tfa"]').is_visible():
                    otp_rejected = True
                    break

            if otp_rejected:
                self.tracer.save(page, f"otp_{attempt:02d}_rejected")
                logger.error("OTP rejected — code incorrect")
                if attempt == max_retries:
                    raise RuntimeError(f"OTP failed after {max_retries} attempts")
                continue

            self.tracer.save(page, f"otp_{attempt:02d}_timeout")
            raise TimeoutError(f"Post-OTP navigation timeout (url={page.url})")

        raise RuntimeError(f"OTP failed after {max_retries} attempts")

    def _wait_for_otp(self, page: Page) -> None:
        """
        Wait for the OTP input field, handling the passkey-selection screen if it appears first.

        After submitting credentials Kraken may show a 2FA method picker
        (data-testid="TwoFactorAuthentication") when the account has a passkey configured.
        We cannot use a passkey, so we click "Authenticator app" to reach the OTP field.
        If the OTP field appears directly (no passkey configured), we proceed immediately.
        """
        page.wait_for_selector(
            '[data-testid="TwoFactorAuthentication"], input[name="tfa"]',
            timeout=60000,
        )
        if page.locator('input[name="tfa"]').is_visible():
            logger.debug("OTP input already visible — no method selection needed")
            return

        logger.info(
            "Passkey selection screen detected — looking for authenticator app option"
        )
        self.tracer.save(page, "2fa_method_selection")
        authenticator_btn = page.locator(
            '[data-testid="TwoFactorAuthentication"] button:has-text("Authenticator app")'
        )
        if not authenticator_btn.is_visible():
            raise NoOTPAuthenticatorError("Authenticator app 2FA method not available")

        authenticator_btn.click()
        logger.info("Authenticator app selected — waiting for OTP input")
        page.wait_for_selector('input[name="tfa"]', timeout=30000)

    def _dismiss_passkey(self, page: Page) -> None:
        """Dismiss the passkey prompt if present."""
        if not page.url.startswith(KrakenPages.PASSKEY):
            return
        try:
            page.click('text="Maybe later"')
            self.tracer.save(page, "passkey_maybe_later_clicked")
            wait_for_url(page, [KrakenPages.DASHBOARD], timeout=10000)
            self.tracer.save(page, "passkey_dismissed")
        except Exception:
            pass

    def _first_login(self, page: Page, credentials: KrakenCredentials) -> Page:
        """Normal login — device-approval expected. Returns page on dashboard."""
        logger.info("First login — no state.json found")
        logger.info(
            "Device approval will be triggered: check your email and click the link"
        )

        page.goto(KrakenPages.LOGIN)
        page.fill('input[name="username"]', credentials.email)
        page.fill('input[name="password"]', credentials.password)
        page.click('button[type="submit"]')
        self.tracer.save(page, "credentials_submitted")

        logger.info("Waiting for OTP field...")
        self._wait_for_otp(page)

        expected = [
            KrakenPages.DASHBOARD,
            KrakenPages.DEVICE_APPROVAL,
            KrakenPages.PASSKEY,
        ]
        self._handle_otp(page, expected_urls=expected)

        if page.url.startswith(KrakenPages.DEVICE_APPROVAL):
            logger.info(
                "Device approval page reached — waiting for email confirmation (up to 5 min)..."
            )
            if not wait_for_url(
                page, [KrakenPages.DASHBOARD, KrakenPages.PASSKEY], timeout=300000
            ):
                self.tracer.save(page, "fail_device_approval_timeout")
                raise TimeoutError(
                    "Device approval timeout — link not clicked within 5 minutes"
                )

        self._dismiss_passkey(page)

        logger.info("Login successful — dashboard reached")
        self.tracer.save(page, "pass_first_login")
        return page

    def _session_valid(self, page: Page) -> bool:
        """Navigate to dashboard and return True if we arrive there (session alive)."""
        logger.info("Checking session validity — navigating to dashboard...")
        try:
            page.goto(
                KrakenPages.DASHBOARD, wait_until="domcontentloaded", timeout=30000
            )
            self.tracer.save(page, "session_check")

            page.wait_for_timeout(3000)
            is_valid = page.url.startswith(KrakenPages.DASHBOARD)
            if is_valid:
                logger.info("Session valid — dashboard reached (url={})", page.url)
            else:
                logger.warning("Session expired — redirected to {}", page.url)
            return is_valid
        except Exception as e:
            logger.warning(
                "Session check error ({}): {} — assuming expired", type(e).__name__, e
            )
            self.tracer.save(page, "session_check_failed")
            return False

    def _device_checked_login(self, page: Page, credentials: KrakenCredentials) -> Page:
        """Re-authenticate — device-approval should not trigger (stored state is used)."""
        logger.info("Re-authenticating with dev-cookie bypass")

        page.goto(KrakenPages.LOGIN)
        page.fill('input[name="username"]', credentials.email)
        page.fill('input[name="password"]', credentials.password)
        page.click('button[type="submit"]')
        self.tracer.save(page, "credentials_submitted")

        logger.info("Waiting for OTP field...")
        self._wait_for_otp(page)

        expected = [
            KrakenPages.DASHBOARD,
            KrakenPages.DEVICE_APPROVAL,
            KrakenPages.PASSKEY,
        ]
        self._handle_otp(page, expected_urls=expected)

        if page.url.startswith(KrakenPages.DEVICE_APPROVAL):
            self.tracer.save(page, "fail_device_approval")
            raise RuntimeError(
                "Device-approval triggered — stored dev cookie is no longer valid."
            )

        self._dismiss_passkey(page)

        logger.info("Login successful — dashboard reached")
        self.tracer.save(page, "pass_dashboard")
        return page

    @contextmanager
    def login(
        self, state_path: Path, credentials: KrakenCredentials
    ) -> Generator[tuple[Page, KrakenInterceptor]]:
        with self.with_browser(state_path) as (context, first_login):
            page = context.new_page()

            interceptor = KrakenInterceptor()
            page.on("response", interceptor)

            self._attach_page_hooks(page)

            if first_login:
                self._first_login(page, credentials)
                logger.info("First login complete — re-run to perform actions")
            elif not self._session_valid(page):
                logger.info("Session expired — re-authenticating")
                page = self._device_checked_login(page, credentials)
            else:
                logger.info("Reusing existing session — login skipped")

            yield page, interceptor
