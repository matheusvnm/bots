import re
from contextlib import contextmanager
from pathlib import Path
from typing import Generator
from urllib.parse import urlparse

from loguru import logger
from patchright.sync_api import BrowserContext, Page, sync_playwright

from components.dtos import Credentials
from components.network import attach_network_logger
from components.trace import PageTracer
from components.utils import wait_for_url
from services.coinbase.constants import CoinbasePages


class CoinbaseAuthenticator:
    def __init__(self, tracer: PageTracer):
        self.tracer = tracer

    @contextmanager
    def with_browser(self, state_file_path: Path) -> Generator[BrowserContext]:
        state_file_path.parent.mkdir(parents=True, exist_ok=True)
        state_file_path.touch(exist_ok=True)
        state_file_path.write_text("{}")

        with sync_playwright() as p:
            browser = p.chromium.launch(
                channel="chrome",
                headless=False,
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
                permissions=["clipboard-read", "clipboard-write"],
                storage_state=str(state_file_path)
            )

            try:
                yield context
            finally:
                context.storage_state(path=str(state_file_path))
                logger.info("Saved storage state → {}", state_file_path)
                browser.close()

    def _attach_page_hooks(self, page: Page) -> None:
        attach_network_logger(page)

        def on_navigated(frame) -> None:
            if frame.parent_frame is not None:
                return
            logger.debug("[NAV] {}", frame.url)
            try:
                parsed = urlparse(frame.url)
                slug = re.sub(r"[^\w]+", "_", f"{parsed.netloc}{parsed.path}").strip("_")[:60]
                self.tracer.save(page, f"nav_{slug}")
            except Exception:
                pass

        page.on("framenavigated", on_navigated)

    def _submit_email(self, page: Page, email: str) -> None:
        logger.info("Filling email...")
        page.wait_for_selector('[data-testid="email-input"]', timeout=30000)

        page.fill('[data-testid="email-input"]', email)
        self.tracer.save(page, "email_filled")

        page.click('[data-testid="email-submit-button"]')
        logger.info("Email submitted")


    def _submit_password(self, page: Page, password: str) -> None:
        logger.info("Waiting for auth method screen...")

        page.wait_for_selector(
            '[data-testid="passkey-auth"], [data-testid="password-input"]',
            timeout=30000,
        )

        if page.locator('[data-testid="password-input"]').is_visible():
            logger.debug("Password input already visible — passkey screen skipped")
            return
        
        logger.info("Passkey screen detected — selecting password method")
        self.tracer.save(page, "passkey_screen")

        logger.info("Waiting")
        page.click('[data-testid="two-factor-button-PASSWORD"]')

        logger.info("Filling password...")

        page.wait_for_selector('[data-testid="password-input"]', timeout=30000)
        page.fill('[data-testid="password-input"]', password)
        self.tracer.save(page, "password_filled")

        page.click('[data-testid="password-submit-button"]')

        page.wait_for_selector('[data-testid="password-input"]', state="hidden", timeout=15000)
        logger.info("Password submitted — form transitioned")

    def _select_2fa_method(self, page: Page) -> None:
        """After password, Coinbase shows the passkey screen again as 2FA.

        Prefers TOTP; falls back to SMS if TOTP is not available.
        """
        logger.info("Waiting for 2FA method selection...")
        self.tracer.save(page, "after_password_state")

        page.wait_for_selector(
            '[data-testid="two-factor-button-TOTP"], '
            '[data-testid="two-factor-button-SMS"], '
            '[data-testid="code-inputs-container"]',
            timeout=60000,
        )

        if page.locator('[data-testid="code-inputs-container"]').is_visible():
            logger.debug("Code input already visible — method selection skipped")
            return

        self.tracer.save(page, "2fa_method_selection")
        if page.locator('[data-testid="two-factor-button-TOTP"]').is_visible():
            logger.info("Selecting TOTP method")
            page.click('[data-testid="two-factor-button-TOTP"]')
            return

        if page.locator('[data-testid="two-factor-button-SMS"]').is_visible():
            logger.info("TOTP not available — selecting SMS method")
            page.click('[data-testid="two-factor-button-SMS"]')
            return 

        raise RuntimeError("No supported 2FA method found (expected TOTP or SMS)")

    def _handle_totp(self, page: Page, max_retries: int = 3) -> None:
        """Prompt for TOTP code, type into the 6 individual digit inputs, retry on rejection."""
        page.wait_for_selector('[data-testid="code-inputs-container"]', timeout=30000)

        def past_totp_screen() -> bool:
            return (
                page.url.startswith(CoinbasePages.DASHBOARD)
                or page.locator('[data-testid="standard-device-verification-confirmation"]').is_visible()
            )

        for attempt in range(1, max_retries + 1):
            if past_totp_screen():
                return

            self.tracer.save(page, f"totp_{attempt:02d}_prompt")
            totp = input(f"[?] TOTP code (attempt {attempt}/{max_retries}): ").strip()

            if past_totp_screen():
                return

            # First input has autocomplete="one-time-code"; click it and type digit by digit.
            # The app auto-advances focus between the 6 inputs.
            page.click("#one-time-code")
            for digit in totp:
                page.keyboard.type(digit)
                page.wait_for_timeout(60)

            self.tracer.save(page, f"totp_{attempt:02d}_submitted")
            logger.info("TOTP submitted (attempt {})", attempt)

            # Wait up to 15s: success = navigation away / device verification appears
            # Rejection = inputs cleared (first input becomes empty again)
            waited = 0
            rejected = False
            while waited < 15000:
                page.wait_for_timeout(1000)
                waited += 1000
                if past_totp_screen():
                    return
                if waited >= 5000:
                    try:
                        first_val = page.locator("#one-time-code").input_value()
                        if first_val == "":
                            rejected = True
                            break
                    except Exception:
                        pass

            if rejected:
                self.tracer.save(page, f"totp_{attempt:02d}_rejected")
                logger.error("TOTP rejected — code was incorrect")
                if attempt == max_retries:
                    raise RuntimeError(f"TOTP failed after {max_retries} attempts")
                continue

            self.tracer.save(page, f"totp_{attempt:02d}_timeout")
            raise TimeoutError(f"No navigation after TOTP submission (url={page.url})")

        raise RuntimeError(f"TOTP failed after {max_retries} attempts")

    def _wait_for_device_verification(self, page: Page) -> None:
        """On first login Coinbase may require device verification via email link."""
        if not page.locator('[data-testid="standard-device-verification-confirmation"]').is_visible():
            return

        logger.info("Device verification required: Please, check your email.")
        self.tracer.save(page, "device_verification")

        if not wait_for_url(page, [CoinbasePages.DASHBOARD], timeout=300000):
            self.tracer.save(page, "fail_device_verification_timeout")
            raise TimeoutError("Device verification timeout.")
        
        logger.info("Device verification complete")

    def _session_valid(self, page: Page) -> bool:
        logger.info("Checking session — navigating to dashboard...")
        try:
            page.goto(CoinbasePages.DASHBOARD, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)
            self.tracer.save(page, "session_check")
    
            valid = page.url.startswith(CoinbasePages.DASHBOARD)
            logger.info("Session valid? %s as we are on %s", valid, page.url)
            return valid

        except Exception as e:
            logger.warning("Session check failed ({}) — assuming expired", e)
            self.tracer.save(page, "session_check_failed")
            return False

    def _do_login(self, page: Page, credentials: Credentials) -> None:
        page.goto(CoinbasePages.SIGNIN)

        self._submit_email(page, credentials.email)
        self._submit_password(page, credentials.password)

        self._select_2fa_method(page)
        self._handle_totp(page)
        self._wait_for_device_verification(page)

        if not wait_for_url(page, [CoinbasePages.DASHBOARD], timeout=30000):
            self.tracer.save(page, "fail_login_timeout")
            raise TimeoutError("Did not reach dashboard after login")

        self.tracer.save(page, "pass_login")
        logger.info("Login successful — dashboard reached")

    def _create_supressed_webauth_page(self, browser_context: BrowserContext) -> Page:
        """" Creates a page with supressed native WebAuthn OS dialog via CDP """
        page = browser_context.new_page()
        cdp = browser_context.new_cdp_session(page)
        cdp.send("WebAuthn.enable", {"enableUI": False})
        self._attach_page_hooks(page)
        return page


    @contextmanager
    def login(
        self, credentials: Credentials
    ) -> Generator[Page, None, None]:
        with self.with_browser(credentials.state_file_path) as context:
            page = self._create_supressed_webauth_page(context)

            if not self._session_valid(page):
                logger.info("First login or session expired.")
                self._do_login(page, credentials)

            yield page
