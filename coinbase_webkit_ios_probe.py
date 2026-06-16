"""
Coinbase WebKit / iOS probe.

Purpose
-------
Faithfully reproduce what the iOS Connect SDK sees inside its offscreen
WKWebView, so we can diagnose why both the network-INTERCEPTION approach
(extension) and the DOM-SCRAPE approach (feat/AUTH-2770-get-balance-flow)
fail on mobile.

It uses the **WebKit** engine (same family as iOS WKWebView), an **iPhone
device descriptor** (mobile UA + viewport + touch), and on a fixed interval
dumps three artefacts the Swift code is currently BLIND to:

  1. screenshots/  -> PNG of the rendered page (proves captcha / mobile layout)
  2. html/         -> full innerHTML of <body> (proves which selectors exist)
  3. network_debug.log -> every request + response body, with the Coinbase
                          GraphQL CashQuery/CryptoQuery responses flagged
                          (proves whether the authoritative balance JSON is
                          reachable at all in WebKit)

It also, on each tick, runs the SAME readiness gate + row selectors the iOS
scraper uses, so we can see DOM-scrape result vs network JSON side by side.

Run (headful so you can solve captcha / 2FA / login):
    cd /Users/smmarques/Github/scrapping/bots
    uv run python coinbase_webkit_ios_probe.py

First run logs you in interactively; session is persisted to
.context/coinbase_webkit/state.json and reused next time.

Flags via env:
    CB_URL        start url (default https://www.coinbase.com/home)
    CB_HEADLESS   "1" to run headless (default headful)
    CB_DEVICE     playwright device name (default "iPhone 14 Pro")
    CB_INTERVAL   seconds between dumps (default 2)
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from urllib.parse import urlparse

from loguru import logger
from playwright.sync_api import (
    BrowserContext,
    Page,
    Response,
    sync_playwright,
)

from probe.netlog import NetworkRecorder, build_row, is_graphql
from probe.runseq import SeqClock
from probe.storage import StateSnapshotter

# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
COINBASE_URL = os.environ.get("CB_URL", "https://www.coinbase.com/home")
HEADLESS = os.environ.get("CB_HEADLESS", "0") == "1"
DEVICE_NAME = os.environ.get("CB_DEVICE", "iPhone 14 Pro")
DUMP_INTERVAL_S = float(os.environ.get("CB_INTERVAL", "2"))

# The readiness gate + row selectors the iOS SCRAPER uses
# (connect-ios .../Coinbase/get-crypto-balance.js / get-cash-balance.js).
IOS_READY_GATE = '[data-testid^="page-container-mobile"]'
IOS_CRYPTO_ROWS = 'div[data-testid^="account-table-row-for-"]'
IOS_CASH_ROWS = 'div[data-testid^="cash-table-row-for-"]'
IOS_CAPTCHA_SEL = 'div[class="ch-title-zone"]'


# --------------------------------------------------------------------------- #
# tracing context + logging (mirrors coinbase_explore.py conventions)
# --------------------------------------------------------------------------- #
@dataclass
class TraceContext:
    bot: str
    user_id: str
    session_id: str

    @cached_property
    def log_dir(self) -> Path:
        return Path("logs") / self.bot / self.user_id / self.session_id


_APP_FMT = (
    "{time:YYYY-MM-DDTHH:mm:ss} | {level:<8} | "
    "{extra[bot]}:{extra[user_id]}:{extra[session_id]} | {message}"
)
_NET_FMT = (
    "{time:YYYY-MM-DDTHH:mm:ss} | "
    "{extra[bot]}:{extra[user_id]}:{extra[session_id]} | {message}"
)


def configure_logging(ctx: TraceContext) -> None:
    logger.remove()
    logger.configure(
        extra={"bot": ctx.bot, "user_id": ctx.user_id, "session_id": ctx.session_id}
    )
    logger.add(
        sys.stdout,
        format=_APP_FMT,
        level="INFO",
        colorize=True,
        filter=lambda r: not r["extra"].get("network_debug", False),
    )
    ctx.log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        ctx.log_dir / "app.log",
        format=_APP_FMT,
        level="DEBUG",
        filter=lambda r: not r["extra"].get("network_debug", False),
    )
    logger.add(
        ctx.log_dir / "network_debug.log",
        format=_NET_FMT,
        level="DEBUG",
        filter=lambda r: r["extra"].get("network_debug", False),
    )


# --------------------------------------------------------------------------- #
# page tracer: screenshots + html
# --------------------------------------------------------------------------- #
class PageTracer:
    def __init__(self, ctx: TraceContext):
        self.ctx = ctx

    def save(self, page: Page, name: str, seq: int) -> None:
        shot = self.ctx.log_dir / "screenshots" / f"{seq:04d}_{name}.png"
        shot.parent.mkdir(parents=True, exist_ok=True)
        try:
            page.screenshot(path=str(shot), full_page=True)
        except Exception as exc:  # noqa: BLE001
            logger.error("screenshot failed {}: {}", shot, exc)

        html = self.ctx.log_dir / "html" / f"{seq:04d}_{name}.html"
        html.parent.mkdir(parents=True, exist_ok=True)
        try:
            html.write_text(page.content(), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.error("html failed {}: {}", html, exc)


# --------------------------------------------------------------------------- #
# network capture: dumps every req/res, flags balance GraphQL responses
# --------------------------------------------------------------------------- #
_nlog = logger.bind(network_debug=True)


def attach_network_logger(
    page: Page, ctx: TraceContext, recorder: NetworkRecorder, clock: SeqClock
) -> None:
    def on_request(request) -> None:
        try:
            body = request.post_data
        except Exception:  # noqa: BLE001
            body = None
        seq = clock.next()
        recorder.write_row(
            build_row(
                seq=seq,
                kind="request",
                method=request.method,
                url=request.url,
                status=None,
                request_headers=dict(request.headers),
                request_body=body,
                response_headers=None,
                response_body=None,
            )
        )

    def on_response(response: Response) -> None:
        req = response.request
        url = req.url
        ctype = (response.headers or {}).get("content-type", "").lower()
        textual = any(
            t in ctype for t in ("json", "text", "javascript", "graphql", "xml")
        )
        body = None
        if textual or is_graphql(url):
            try:
                raw = response.body()  # bytes
            except Exception:  # noqa: BLE001
                raw = None
            if raw is not None and len(raw) <= 5_000_000:
                try:
                    body = raw.decode("utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    body = None
            elif raw is not None:
                body = f"<{len(raw)} bytes omitted>"
        try:
            req_body = req.post_data
        except Exception:  # noqa: BLE001
            req_body = None
        seq = clock.next()
        recorder.write_row(
            build_row(
                seq=seq,
                kind="response",
                method=req.method,
                url=url,
                status=response.status,
                request_headers=dict(req.headers),
                request_body=req_body,
                response_headers=dict(response.headers),
                response_body=body,
            )
        )
        if is_graphql(url):
            if not body or body.startswith("<"):
                logger.warning(
                    "GraphQL response body unavailable for {} (status={})",
                    url,
                    response.status,
                )
            else:
                op = recorder.maybe_dump_graphql(url, body, response.status)
                if op:
                    logger.warning(
                        "GraphQL captured op={} status={}", op, response.status
                    )

    page.on("request", on_request)
    page.on("response", on_response)
    page.on(
        "requestfailed",
        lambda r: _nlog.debug("[REQ_ERR] {} {} {}", r.method, r.url, r.failure),
    )


# --------------------------------------------------------------------------- #
# iOS scraper emulation: run the SAME gate/selectors the Swift JS uses
# --------------------------------------------------------------------------- #
_IOS_PROBE_JS = """
() => {
  const out = {};
  out.url = location.href;
  out.path = location.pathname;
  out.readyGate = !!document.querySelector(%s);
  out.captchaVisible = !!document.querySelector(%s);
  out.cryptoRows = document.querySelectorAll(%s).length;
  out.cashRows = document.querySelectorAll(%s).length;
  // sample the first row's span texts the scraper would read by index
  const firstCrypto = document.querySelector(%s);
  out.firstCryptoSpans = firstCrypto
    ? Array.from(firstCrypto.querySelectorAll('span')).slice(0, 4).map(s => s.textContent)
    : null;
  return out;
}
""" % (
    json.dumps(IOS_READY_GATE),
    json.dumps(IOS_CAPTCHA_SEL),
    json.dumps(IOS_CRYPTO_ROWS),
    json.dumps(IOS_CASH_ROWS),
    json.dumps(IOS_CRYPTO_ROWS),
)


def probe_ios_dom(page: Page) -> dict:
    try:
        return page.evaluate(_IOS_PROBE_JS)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


# --------------------------------------------------------------------------- #
# session / browser lifecycle
# --------------------------------------------------------------------------- #
@contextmanager
def webkit_context(state_file: Path, tracer_device: dict):
    first_login = not state_file.exists()
    if first_login:
        logger.info("no state file at {} - fresh session (log in manually)", state_file)
        state_file.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.webkit.launch(headless=HEADLESS)
        kwargs = dict(tracer_device)
        if not first_login:
            kwargs["storage_state"] = str(state_file)
        context = browser.new_context(**kwargs)
        try:
            yield context, first_login
        finally:
            try:
                context.storage_state(path=str(state_file))
                logger.info("saved storage state -> {}", state_file)
            except Exception as exc:  # noqa: BLE001
                logger.error("failed saving state: {}", exc)
            browser.close()


def main() -> None:
    ctx = TraceContext("coinbase_webkit", user_id="001", session_id=uuid.uuid4().hex[:8])
    configure_logging(ctx)
    tracer = PageTracer(ctx)

    with sync_playwright() as _p:
        device = dict(_p.devices.get(DEVICE_NAME, _p.devices["iPhone 14 Pro"]))
    logger.info(
        "engine=WebKit device={!r} viewport={} headless={}",
        DEVICE_NAME,
        device.get("viewport"),
        HEADLESS,
    )

    state_file = Path(".context") / "coinbase_webkit" / "user" / "001" / "state.json"

    with webkit_context(state_file, device) as (context, first_login):
        page = context.new_page()

        clock = SeqClock(
            meta={
                "engine": "webkit",
                "device": DEVICE_NAME,
                "viewport": device.get("viewport"),
                "headless": HEADLESS,
                "start_url": COINBASE_URL,
            }
        )
        recorder = NetworkRecorder(ctx.log_dir)
        snapshotter = StateSnapshotter(ctx.log_dir, context)
        attach_network_logger(page, ctx, recorder, clock)

        def capture(kind: str) -> None:
            seq = clock.next()
            parsed = urlparse(page.url)
            slug = re.sub(r"[^\w]+", "_", f"{parsed.netloc}{parsed.path}").strip("_")[:60]
            clock.record(seq, kind=kind, url=page.url, label=slug)
            tracer.save(page, f"{kind}_{slug}", seq)
            snapshotter.snapshot(page, seq)

        def on_load() -> None:
            try:
                page.wait_for_timeout(1500)
                capture("nav")
            except Exception as exc:  # noqa: BLE001
                logger.debug("on_load capture failed: {}", exc)

        page.on("load", on_load)

        logger.info("navigating to {}", COINBASE_URL)
        page.goto(COINBASE_URL, wait_until="domcontentloaded")

        if first_login:
            logger.warning(
                "FIRST RUN: log in manually in the browser window "
                "(solve any captcha/2FA). Probing continues meanwhile; "
                "Ctrl-C to stop and persist session."
            )

        try:
            while True:
                page.wait_for_timeout(int(DUMP_INTERVAL_S * 1000))
                capture("tick")
                dom = probe_ios_dom(page)
                logger.info("iOS-DOM probe: {}", json.dumps(dom, ensure_ascii=False))
        except KeyboardInterrupt:
            logger.info("stopping on Ctrl-C")
        finally:
            clock.write_manifest(ctx.log_dir / "manifest.json")
            logger.info("wrote manifest -> {}", ctx.log_dir / "manifest.json")


if __name__ == "__main__":
    main()
