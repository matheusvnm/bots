



COINBASE_URL="https://www.coinbase.com/home"


"""
Application logger setup using loguru.

Call configure_logging() once at startup (run.py).
All other modules do:  from loguru import logger

Session context is bound via logger.contextualize() in run.py,
making bot / user_id / session_id available on every log record.
"""

from contextlib import contextmanager
import re
import sys
from pathlib import Path
from urllib.parse import urlparse
import uuid

from loguru import logger

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from patchright.sync_api import Page, sync_playwright, Response, BrowserContext


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

        screenshot_path = self.ctx.log_dir / "screenshots" / f"{n:03d}_{name}.png"
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            page.screenshot(path=str(screenshot_path))
            logger.opt(depth=1).debug("Screenshot → {}", screenshot_path)
        except Exception:
            logger.opt(depth=1).error("Failed to take screenshot: {}", screenshot_path)

        html_path = self.ctx.log_dir / "html" / f"{n:03d}_{name}.html"
        html_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            content = page.inner_html("body")
            html_path.write_text(content, encoding="utf-8")
            logger.opt(depth=1).debug("HTML saved → {}", html_path)
        except Exception:
            logger.opt(depth=1).error("Failed to save HTML: {}", html_path)


# Stdout/file format — uses extra fields set by logger.configure() defaults
# and overridden per-session via logger.contextualize()
_APP_FMT = (
    "{time:YYYY-MM-DDTHH:mm:ss} | {level:<8} | "
    "{extra[bot]}:{extra[user_id]}:{extra[session_id]} | "
    "{name} | {message}"
)

# Network sink format — leaner, no level/name noise
_NET_FMT = (
    "{time:YYYY-MM-DDTHH:mm:ss} | "
    "{extra[bot]}:{extra[user_id]}:{extra[session_id]} | "
    "{message}"
)


def configure_logging() -> None:
    """Configure loguru sinks. Idempotent — safe to call more than once."""
    logger.remove()

    # Set defaults so the format works even before contextualize() is entered
    logger.configure(extra={"bot": "-", "user_id": "-", "session_id": "-"})

    # Stdout — colored, all app records
    logger.add(
        sys.stdout,
        format=_APP_FMT,
        level="INFO",
        colorize=True,
        filter=lambda r: not r["extra"].get("network_debug", False),
    )

    Path("logs").mkdir(parents=True, exist_ok=True)

    # Bot log file — app records only
    logger.add(
        "logs/bot.log",
        format=_APP_FMT,
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
        filter=lambda r: not r["extra"].get("network_debug", False),
    )

    # Network debug file — network_debug=True records only
    logger.add(
        "logs/network_debug.log",
        format=_NET_FMT,
        level="DEBUG",
        rotation="50 MB",
        retention="3 days",
        filter=lambda r: r["extra"].get("network_debug", False),
    )

configure_logging()

_nlog = logger.bind(network_debug=True)


def attach_network_logger(page: Page) -> None:
    """Attach network debug logging to *page*."""

    def on_request(request) -> None:
        try:
            data = request.post_data
        except Exception:
            data = "<binary or unavailable>"
        try:
            _nlog.debug("[REQ]  {} {}  data={}", request.method, request.url, data)
        except Exception:
            pass

    def on_response(response: Response) -> None:
        req = response.request
        try:
            data = response.text()
        except Exception:
            data = "<binary or unavailable>"
        try:
            _nlog.debug(
                "[RES]  {} {}  status={}  data={}",
                req.method,
                req.url,
                response.status,
                data,
            )
        except Exception:
            pass

    def on_request_failed(request) -> None:
        try:
            data = request.post_data
        except Exception:
            data = "<binary or unavailable>"
        try:
            _nlog.debug("[REQ_ERR]  {} {}  data={}", request.method, request.url, data)
        except Exception:
            pass

    page.on("request", on_request)
    page.on("response", on_response)
    page.on("requestfailed", on_request_failed)



class CoinbaseAuthenticator:
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

        def on_load() -> None:
            logger.debug("[LOAD] {}", page.url)
            try:
                page.wait_for_timeout(2000)
                parsed = urlparse(page.url)
                slug = re.sub(r"[^\w]+", "_", f"{parsed.netloc}{parsed.path}").strip(
                    "_"
                )[:60]
                self.tracer.save(page, f"nav_{slug}")
            except Exception:
                pass

        page.on("load", on_load)






if __name__ == "__main__":
    ctx = TraceContext(
        "coinbase", user_id="001", session_id=uuid.uuid4().hex
    )

    tracer = PageTracer(ctx=ctx)
    autenticator = CoinbaseAuthenticator(tracer=tracer)
    state_file_path = Path(".context") / "coinbase" / "user" / "001" / "state.json"

    browser: BrowserContext
    with autenticator.with_browser(state_file_path) as (browser, _):
        page = browser.new_page()

        autenticator._attach_page_hooks(page)

        page.goto(COINBASE_URL)
        while True:
            page.wait_for_timeout(2000)
            tracer.save(page, "auto")
    