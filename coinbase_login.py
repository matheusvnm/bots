"""
Coinbase login automation.

Usage:
    python coinbase_login.py [--user <id>]

Flow:
    1. Enter email
    2. Dismiss passkey prompt — select password method
    3. Enter password
    4. Select TOTP as 2FA method
    5. Enter TOTP code (6 individual digit inputs)
    6. Device verification (first login only — click link in email)
    7. Dashboard reached → session saved
"""

import argparse
import re
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Generator
from urllib.parse import urlparse

from loguru import logger
from patchright.sync_api import Page, sync_playwright
from pydantic_settings import BaseSettings, SettingsConfigDict


# ── Pages ─────────────────────────────────────────────────────────────────────

class CoinbasePages:
    SIGNIN    = "https://login.coinbase.com/signin"
    DASHBOARD = "https://www.coinbase.com/home"


# ── Settings ──────────────────────────────────────────────────────────────────

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
    user_email: str
    user_password: str
    context_dir: Path = Path(".context")


# ── Logging ───────────────────────────────────────────────────────────────────

_APP_FMT = (
    "{time:YYYY-MM-DDTHH:mm:ss} | {level:<8} | "
    "{extra[bot]}:{extra[user_id]}:{extra[session_id]} | "
    "{name} | {message}"
)
_NET_FMT = (
    "{time:YYYY-MM-DDTHH:mm:ss} | "
    "{extra[bot]}:{extra[user_id]}:{extra[session_id]} | "
    "{message}"
)


def configure_logging() -> None:
    logger.remove()
    logger.configure(extra={"bot": "-", "user_id": "-", "session_id": "-"})
    logger.add(
        sys.stdout,
        format=_APP_FMT,
        level="INFO",
        colorize=True,
        filter=lambda r: not r["extra"].get("network_debug", False),
    )
    Path("logs").mkdir(parents=True, exist_ok=True)
    logger.add(
        "logs/bot.log",
        format=_APP_FMT,
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
        filter=lambda r: not r["extra"].get("network_debug", False),
    )
    logger.add(
        "logs/network_debug.log",
        format=_NET_FMT,
        level="DEBUG",
        rotation="50 MB",
        retention="3 days",
        filter=lambda r: r["extra"].get("network_debug", False),
    )


# ── Tracing ───────────────────────────────────────────────────────────────────

@dataclass
class TraceContext:
    bot: str
    user_id: str
    session_id: str

    @cached_property
    def log_dir(self) -> Path:
        return Path("logs") / self.bot / self.user_id / self.session_id


class PageTracer:
    def __init__(self, ctx: TraceContext):
        self.counter = 0
        self.ctx = ctx

    def save(self, page: Page, name: str) -> None:
        n = self.counter
        self.counter += 1

        ss_path = self.ctx.log_dir / "screenshots" / f"{n:03d}_{name}.png"
        ss_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            page.screenshot(path=str(ss_path))
            logger.debug("Screenshot → {}", ss_path)
        except Exception:
            logger.debug("Failed screenshot: {}", ss_path)

        html_path = self.ctx.log_dir / "html" / f"{n:03d}_{name}.html"
        html_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            html_path.write_text(page.inner_html("body"), encoding="utf-8")
            logger.debug("HTML saved → {}", html_path)
        except Exception:
            logger.debug("Failed HTML: {}", html_path)


# ── Network ───────────────────────────────────────────────────────────────────

def attach_network_logger(page: Page) -> None:
    _nlog = logger.bind(network_debug=True)

    def on_request(req) -> None:
        try:
            data = req.post_data
        except Exception:
            data = "<binary>"
        try:
            _nlog.debug("[REQ]  {} {}  data={}", req.method, req.url, data)
        except Exception:
            pass

    def on_response(res) -> None:
        try:
            data = res.text()
        except Exception:
            data = "<binary>"
        try:
            _nlog.debug(
                "[RES]  {} {}  status={}  data={}",
                res.request.method, res.url, res.status, data,
            )
        except Exception:
            pass

    def on_request_failed(req) -> None:
        try:
            _nlog.debug("[REQ_ERR]  {} {}", req.method, req.url)
        except Exception:
            pass

    page.on("request", on_request)
    page.on("response", on_response)
    page.on("requestfailed", on_request_failed)


# ── Helpers ───────────────────────────────────────────────────────────────────

def wait_for_url(page: Page, expected: list[str], timeout: int = 60000) -> bool:
    waited = 0
    while waited < timeout:
        page.wait_for_timeout(1000)
        if any(page.url.startswith(u) for u in expected):
            return True
        waited += 1000
    return False


# ── Credentials ───────────────────────────────────────────────────────────────

@dataclass
class CoinbaseCredentials:
    email: str
    password: str


# ── Authenticator ─────────────────────────────────────────────────────────────

class CoinbaseAuthenticator:
    def __init__(self, tracer: PageTracer):
        self.tracer = tracer

    @contextmanager
    def with_browser(self, state_file_path: Path):
        first_login = not state_file_path.exists()
        if first_login:
            logger.info("No existing state at {} — fresh context", state_file_path)
            state_file_path.parent.mkdir(parents=True, exist_ok=True)

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
            ctx_kwargs = dict(
                viewport={"width": 1440, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/145.0.0.0 Safari/537.36"
                ),
                locale="en-US",
                timezone_id="America/New_York",
                permissions=["clipboard-read", "clipboard-write"],
            )
            if not first_login:
                ctx_kwargs["storage_state"] = str(state_file_path)
            context = browser.new_context(**ctx_kwargs)
            try:
                yield context, first_login
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

    def _select_password_method(self, page: Page) -> None:
        """After email, Coinbase shows passkey screen. Switch to password auth."""
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

    def _submit_password(self, page: Page, password: str) -> None:
        logger.info("Filling password...")
        page.wait_for_selector('[data-testid="password-input"]', timeout=30000)
        page.fill('[data-testid="password-input"]', password)
        self.tracer.save(page, "password_filled")
        page.click('[data-testid="password-submit-button"]')
        # Wait for the password form to disappear — this confirms the page
        # has transitioned before we try to interact with the next screen.
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
        elif page.locator('[data-testid="two-factor-button-SMS"]').is_visible():
            logger.info("TOTP not available — selecting SMS method")
            page.click('[data-testid="two-factor-button-SMS"]')
        else:
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
        logger.info(
            "Device verification required — check your email and click the link (up to 5 min)..."
        )
        self.tracer.save(page, "device_verification")
        if not wait_for_url(page, [CoinbasePages.DASHBOARD], timeout=300000):
            self.tracer.save(page, "fail_device_verification_timeout")
            raise TimeoutError("Device verification timeout — link not clicked within 5 minutes")
        logger.info("Device verification complete")

    def _session_valid(self, page: Page) -> bool:
        logger.info("Checking session — navigating to dashboard...")
        try:
            page.goto(CoinbasePages.DASHBOARD, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)
            self.tracer.save(page, "session_check")
            valid = page.url.startswith(CoinbasePages.DASHBOARD)
            if valid:
                logger.info("Session valid (url={})", page.url)
            else:
                logger.warning("Session expired — redirected to {}", page.url)
            return valid
        except Exception as e:
            logger.warning("Session check failed ({}) — assuming expired", e)
            self.tracer.save(page, "session_check_failed")
            return False

    def _do_login(self, page: Page, credentials: CoinbaseCredentials) -> None:
        page.goto(CoinbasePages.SIGNIN)
        self._submit_email(page, credentials.email)
        self._select_password_method(page)
        self._submit_password(page, credentials.password)
        self._select_2fa_method(page)
        self._handle_totp(page)
        self._wait_for_device_verification(page)
        if not wait_for_url(page, [CoinbasePages.DASHBOARD], timeout=30000):
            self.tracer.save(page, "fail_login_timeout")
            raise TimeoutError("Did not reach dashboard after login")
        self.tracer.save(page, "pass_login")
        logger.info("Login successful — dashboard reached")

    @contextmanager
    def login(
        self, state_path: Path, credentials: CoinbaseCredentials
    ) -> Generator[Page, None, None]:
        with self.with_browser(state_path) as (context, first_login):
            page = context.new_page()
            # Suppress native WebAuthn/passkey OS dialog via CDP.
            # enableUI=False makes Chrome handle WebAuthn internally without
            # showing the system dialog — auth fails silently, Coinbase falls
            # back to TOTP. This operates at the DevTools Protocol level.
            cdp = context.new_cdp_session(page)
            cdp.send("WebAuthn.enable", {"enableUI": False})
            self._attach_page_hooks(page)

            if first_login:
                logger.info("First login — running full auth flow")
                self._do_login(page, credentials)
            elif not self._session_valid(page):
                logger.info("Session expired — re-authenticating")
                self._do_login(page, credentials)
            else:
                logger.info("Reusing existing session — login skipped")

            yield page


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Coinbase login bot")
    parser.add_argument("--user", default="001", help="User ID for state/log isolation")
    args = parser.parse_args()

    configure_logging()
    settings = Settings()

    ctx = TraceContext("coinbase", user_id=args.user, session_id=uuid.uuid4().hex)
    tracer = PageTracer(ctx=ctx)

    with logger.contextualize(bot="coinbase", user_id=args.user, session_id=ctx.session_id):
        authenticator = CoinbaseAuthenticator(tracer=tracer)
        state_path = Path(settings.context_dir) / "coinbase" / "user" / args.user / "state.json"
        credentials = CoinbaseCredentials(
            email=settings.user_email,
            password=settings.user_password,
        )

        with authenticator.login(state_path, credentials) as page:
            from coinbase_deposit import CoinbaseDeposit
            CoinbaseDeposit(page, tracer).run()
