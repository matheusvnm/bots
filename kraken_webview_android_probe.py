"""
Kraken Android System WebView probe.

Purpose
-------
Reproduce, on the desktop, the behaviour of Kraken's authenticated web login
as it is seen from inside an **Android System WebView** (the Chromium-based
engine an Android Connect SDK embeds), so we can record and diff it against a
plain desktop/mobile Chrome session.

Why a separate probe from the iOS one
-------------------------------------
`kraken_webkit_ios_probe.py` launches the **WebKit** engine to emulate an iOS
WKWebView. Android is different: its WebView is **Chromium**, and -- crucially
for the AUTH-3694 login failure we are chasing -- a real Android WebView sends
a distinctive request fingerprint that WKWebView does NOT:

  * User-Agent:        ...Android <v>; <model>... Chrome/<v> Mobile Safari/537.36
  * sec-ch-ua:         "Android WebView";v="145" brand in the client hints
  * sec-ch-ua-mobile:  ?1
  * sec-ch-ua-platform:"Android"
  * x-requested-with:  <the host app package, e.g. com.zerohash.funddemo>

On device, Kraken's POST .../account/settings/tfa returns 401
{"errorClass":"Auth","msg":"Invalid sign-in"} for a byte-identical payload that
succeeds (200) in desktop Chrome. This probe lets us capture that fingerprint
LIVE (fresh session, fresh x-pow) and, via ANDROID_WEBVIEW_FINGERPRINT=0, run
the SAME flow as plain mobile Chrome for a clean A/B in one tool.

What it records (same layout as the iOS probe, so artefacts diff cleanly):
  1. screenshots/  -> full-page PNG of the rendered page
  2. html/         -> full page HTML
  3. network.jsonl -> every request + response (structured, one row each)
  4. responses/    -> every JSON / XHR response body in its own file
  5. storage/      -> cookies + localStorage + sessionStorage + IndexedDB
  6. manifest.json -> seq-indexed run manifest correlating all artefacts

Navigation is PASSIVE: you drive the browser (log in, solve captcha/2FA),
the probe auto-captures on every page load and on each tick.

Run (headful so you can log in / solve captcha / 2FA):
    cd /Users/smmarques/Github/scrapping/bots
    uv run python kraken_webview_android_probe.py

Flags via env:
    KRAKEN_URL                   start url (default https://id.kraken.com/sign-in)
    KRAKEN_HEADLESS              "1" to run headless (default headful)
    KRAKEN_DEVICE                playwright android device (default "Pixel 7")
    KRAKEN_INTERVAL              seconds between dumps (default 2)
    ANDROID_WEBVIEW_FINGERPRINT  "1" (default) send Android-WebView headers;
                                 "0" run as plain mobile Chrome (A/B control)
    ANDROID_WEBVIEW_PACKAGE      x-requested-with value
                                 (default com.zerohash.funddemo)
    ANDROID_WEBVIEW_UA           override the User-Agent entirely
    KRAKEN_STATE_RESET           "1" ignore any saved session (force fresh login)

    Header ISOLATION (only when ANDROID_WEBVIEW_FINGERPRINT=1; each defaults ON):
    WV_SEND_SEC_CH_UA            "0" drop the "Android WebView" sec-ch-ua brand
    WV_SEND_XRW                  "0" drop x-requested-with
    WV_SEND_UA                   "0" drop the WebView UA (use plain device UA)
    -> flip exactly ONE to "0" per live login; if the .../settings/tfa POST then
       returns 200, that dropped header is the one Kraken rejects.

Note: session is persisted per-fingerprint so the WebView run and the Chrome
control never share cookies. Deleting .context/kraken_webview_android/ forces
a fresh login.
"""

from __future__ import annotations

import json
import os
import re
import select
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import cached_property
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from loguru import logger
from playwright.sync_api import (
    Page,
    Response,
    sync_playwright,
)

# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
KRAKEN_URL = os.environ.get("KRAKEN_URL", "https://id.kraken.com/sign-in")
HEADLESS = os.environ.get("KRAKEN_HEADLESS", "0") == "1"
DEVICE_NAME = os.environ.get("KRAKEN_DEVICE", "Pixel 7")
DUMP_INTERVAL_S = float(os.environ.get("KRAKEN_INTERVAL", "2"))
STATE_RESET = os.environ.get("KRAKEN_STATE_RESET", "0") == "1"

# The whole point of this probe: emulate an Android System WebView fingerprint.
WEBVIEW_FINGERPRINT = os.environ.get("ANDROID_WEBVIEW_FINGERPRINT", "1") == "1"
WEBVIEW_PACKAGE = os.environ.get("ANDROID_WEBVIEW_PACKAGE", "com.zerohash.funddemo")

# --- header isolation switches (only meaningful when WEBVIEW_FINGERPRINT=1) ---
# Each defaults to ON (the real-WebView value). Set to "0" to DROP just that one
# tell while keeping the rest of the WebView fingerprint, so a single live login
# pins exactly which header Kraken rejects. Fresh x-pow per run => no replay.
WV_SEND_SEC_CH_UA = os.environ.get("WV_SEND_SEC_CH_UA", "1") == "1"
WV_SEND_XRW = os.environ.get("WV_SEND_XRW", "1") == "1"
WV_SEND_UA = os.environ.get("WV_SEND_UA", "1") == "1"
# Force an Origin header (real on-device WebView XHR carries
# Origin: https://id.kraken.com; the earlier probe runs did NOT, which is the
# last uncontrolled variable between the passing probe and the failing device).
WV_FORCE_ORIGIN = os.environ.get("WV_FORCE_ORIGIN", "0")  # "" = off, else value
if WV_FORCE_ORIGIN == "1":
    WV_FORCE_ORIGIN = "https://id.kraken.com"

# Exact UA observed on the Pixel emulator during the AUTH-3694 investigation.
_DEFAULT_WEBVIEW_UA = (
    "Mozilla/5.0 (Linux; Android 16; Pixel 9) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/150.0.0.0 Mobile Safari/537.36"
)
WEBVIEW_UA = os.environ.get("ANDROID_WEBVIEW_UA", _DEFAULT_WEBVIEW_UA)

# The client-hint bundle a REAL Android WebView emits. Stock Chromium under
# Playwright reports "Chromium"/"Google Chrome" brands and NEVER the
# "Android WebView" brand, so we force these explicitly. These are the prime
# suspect for the 401 -- Kraken can read them server-side even though there is
# no JS/WebView API to change them on device.
_WEBVIEW_CLIENT_HINTS = {
    "sec-ch-ua": '"Not:A-Brand";v="99", "Android WebView";v="145", "Chromium";v="145"',
    "sec-ch-ua-mobile": "?1",
    "sec-ch-ua-platform": '"Android"',
}

# Response bodies larger than this are recorded as a placeholder, not stored.
_MAX_BODY_BYTES = 5_000_000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# tracing context + logging (mirrors kraken_webkit_ios_probe.py conventions)
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
# seq clock + run manifest (inlined)
# --------------------------------------------------------------------------- #
@dataclass
class SeqClock:
    meta: dict[str, Any]
    _n: int = 0
    index: list[dict[str, Any]] = field(default_factory=list)
    started_at: str = field(default_factory=_now_iso)

    def next(self) -> int:
        s = self._n
        self._n += 1
        return s

    def record(
        self, seq: int, *, kind: str, url: str, label: str | None = None
    ) -> None:
        self.index.append(
            {"seq": seq, "ts": _now_iso(), "kind": kind, "url": url, "label": label}
        )

    def write_manifest(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "meta": self.meta,
            "started_at": self.started_at,
            "ended_at": _now_iso(),
            "index": self.index,
        }
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )


# --------------------------------------------------------------------------- #
# page tracer: screenshots + html (inlined)
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
# storage snapshotter: cookies + web storage + indexeddb (inlined)
# --------------------------------------------------------------------------- #
_STORAGE_JS = """
() => {
  const dump = (s) => {
    const o = {};
    for (let i = 0; i < s.length; i++) { const k = s.key(i); o[k] = s.getItem(k); }
    return o;
  };
  return { local: dump(window.localStorage), session: dump(window.sessionStorage) };
}
"""

_INDEXEDDB_JS = """
async () => {
  if (!window.indexedDB || !indexedDB.databases) return [];
  const dbs = await indexedDB.databases();
  const out = [];
  for (const meta of dbs) {
    if (!meta.name) continue;
    const stores = await new Promise((resolve) => {
      const req = indexedDB.open(meta.name);
      req.onsuccess = () => {
        const db = req.result;
        const names = Array.from(db.objectStoreNames);
        db.close();
        resolve(names);
      };
      req.onerror = () => resolve([]);
    });
    out.push({ name: meta.name, version: meta.version, stores });
  }
  return out;
}
"""


class StateSnapshotter:
    def __init__(self, log_dir: Path, context: Any):
        self.dir = log_dir / "storage"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.context = context

    def _write(self, seq: int, name: str, data: Any) -> None:
        path = self.dir / f"{seq:04d}_{name}.json"
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def snapshot(self, page: Any, seq: int) -> None:
        try:
            self._write(seq, "cookies", self.context.cookies())
        except Exception:  # noqa: BLE001
            self._write(seq, "cookies", {"error": "cookies() failed"})

        try:
            storage = page.evaluate(_STORAGE_JS)
        except Exception as exc:  # noqa: BLE001
            storage = {"local": {"error": str(exc)}, "session": {}}
        self._write(seq, "local", storage.get("local", {}))
        self._write(seq, "session", storage.get("session", {}))

        try:
            idb = page.evaluate(_INDEXEDDB_JS)
        except Exception as exc:  # noqa: BLE001
            idb = {"error": str(exc)}
        self._write(seq, "indexeddb", idb)


# --------------------------------------------------------------------------- #
# network recorder: network.jsonl + generic JSON/XHR response splitting
# --------------------------------------------------------------------------- #
def _build_row(
    *,
    seq: int,
    kind: str,
    method: str,
    url: str,
    status: int | None,
    request_headers: dict[str, str] | None,
    request_body: str | None,
    response_headers: dict[str, str] | None,
    response_body: str | None,
) -> dict[str, Any]:
    return {
        "seq": seq,
        "ts": _now_iso(),
        "kind": kind,
        "method": method,
        "url": url,
        "status": status,
        "request_headers": request_headers,
        "request_body": request_body,
        "response_headers": response_headers,
        "response_body": response_body,
    }


def _slug_from_url(url: str) -> str:
    parsed = urlparse(url)
    raw = f"{parsed.path}"
    slug = re.sub(r"[^\w]+", "_", raw).strip("_")
    return slug[:80] or "root"


class NetworkRecorder:
    """Appends structured rows to network.jsonl and splits JSON/XHR bodies."""

    def __init__(self, log_dir: Path):
        self.jsonl_path = log_dir / "network.jsonl"
        self.resp_dir = log_dir / "responses"
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        self.resp_dir.mkdir(parents=True, exist_ok=True)
        self._resp_seq = 0

    def write_row(self, row: dict[str, Any]) -> None:
        with self.jsonl_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def dump_response(
        self, url: str, body: str | None, status: int | None
    ) -> str | None:
        """Write a JSON/XHR response body to its own file. Returns the slug."""
        if not body or body.startswith("<"):
            return None
        self._resp_seq += 1
        slug = _slug_from_url(url)
        prefix = self.resp_dir / f"{self._resp_seq:03d}_{status}_{slug}"
        try:
            parsed = json.loads(body)
            (prefix.with_suffix(".json")).write_text(
                json.dumps(parsed, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except (json.JSONDecodeError, TypeError):
            (prefix.with_suffix(".raw.txt")).write_text(body, encoding="utf-8")
        return slug


# --------------------------------------------------------------------------- #
# network capture: dumps every req/res, splits every JSON/XHR response
# --------------------------------------------------------------------------- #
_nlog = logger.bind(network_debug=True)

# Auth endpoints we most care about for the AUTH-3694 401 -- surfaced loudly.
_AUTH_URL_HINT = re.compile(r"/account/settings/tfa|/auth|/sign-?in|/login", re.I)


def _looks_textual(content_type: str) -> bool:
    return any(
        t in content_type
        for t in ("json", "text", "javascript", "graphql", "xml")
    )


def _looks_json(content_type: str) -> bool:
    return "json" in content_type or "graphql" in content_type


def attach_network_logger(
    page: Page, recorder: NetworkRecorder, clock: SeqClock
) -> None:
    def on_request(request) -> None:
        try:
            body = request.post_data
        except Exception:  # noqa: BLE001
            body = None
        seq = clock.next()
        recorder.write_row(
            _build_row(
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
        body = None
        if _looks_textual(ctype):
            try:
                raw = response.body()  # bytes
            except Exception:  # noqa: BLE001
                raw = None
            if raw is not None and len(raw) <= _MAX_BODY_BYTES:
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
            _build_row(
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
        # Split every JSON/XHR response into its own file for easy inspection.
        if _looks_json(ctype):
            slug = recorder.dump_response(url, body, response.status)
            if slug:
                logger.info(
                    "JSON response captured {} status={}", slug, response.status
                )
        # Shout about the auth calls we are hunting so they are easy to spot
        # live and to correlate with the on-device 401.
        if _AUTH_URL_HINT.search(url):
            level = "ERROR" if response.status >= 400 else "SUCCESS"
            logger.log(
                level,
                "AUTH {} {} -> {} | sec-ch-ua={!r} x-requested-with={!r}",
                req.method,
                url,
                response.status,
                dict(req.headers).get("sec-ch-ua"),
                dict(req.headers).get("x-requested-with"),
            )

    page.on("request", on_request)
    page.on("response", on_response)
    page.on(
        "requestfailed",
        lambda r: _nlog.debug("[REQ_ERR] {} {} {}", r.method, r.url, r.failure),
    )


# --------------------------------------------------------------------------- #
# generic DOM discovery: no hardcoded selectors, help us FIND them
# --------------------------------------------------------------------------- #
_DOM_DISCOVERY_JS = """
() => {
  const out = {};
  out.url = location.href;
  out.path = location.pathname;
  out.title = document.title;

  const tids = new Set();
  document.querySelectorAll('[data-testid]').forEach((el) => {
    const v = el.getAttribute('data-testid');
    if (v) tids.add(v);
  });
  out.dataTestIds = Array.from(tids).slice(0, 100);

  const countSel = (sel) => document.querySelectorAll(sel).length;
  out.counts = {
    tables: countSel('table'),
    tableRows: countSel('tr'),
    listItems: countSel('li'),
    roleRows: countSel('[role="row"]'),
    roleGrid: countSel('[role="grid"], [role="table"]'),
    inputs: countSel('input'),
    buttons: countSel('button'),
  };

  const texts = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode()) && texts.length < 40) {
    const t = (node.textContent || '').trim();
    if (t && t.length <= 40) texts.push(t);
  }
  out.textSamples = texts;

  return out;
}
"""

# What the page itself sees for its own UA / client hints -- lets us confirm
# the WebView fingerprint reached the JS layer, not just the network layer.
_FINGERPRINT_PROBE_JS = """
async () => {
  const uad = navigator.userAgentData || null;
  let high = null;
  if (uad && uad.getHighEntropyValues) {
    try {
      high = await uad.getHighEntropyValues([
        "platform","platformVersion","model","uaFullVersion","fullVersionList","mobile"
      ]);
    } catch (e) { high = { error: String(e) }; }
  }
  return {
    userAgent: navigator.userAgent,
    platform: navigator.platform,
    vendor: navigator.vendor,
    maxTouchPoints: navigator.maxTouchPoints,
    uaData: uad
      ? { brands: uad.brands, mobile: uad.mobile, platform: uad.platform }
      : null,
    uaDataHighEntropy: high,
  };
}
"""


def discover_dom(page: Page) -> dict:
    try:
        return page.evaluate(_DOM_DISCOVERY_JS)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def probe_fingerprint(page: Page) -> dict:
    try:
        return page.evaluate(_FINGERPRINT_PROBE_JS)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


# --------------------------------------------------------------------------- #
# session / browser lifecycle -- CHROMIUM (Android WebView is Chromium)
# --------------------------------------------------------------------------- #
def _build_context_kwargs(device: dict) -> dict:
    """Assemble new_context kwargs emulating an Android WebView (or, when the
    fingerprint is disabled, plain mobile Chrome)."""
    kwargs: dict[str, Any] = {
        "viewport": device.get("viewport"),
        "device_scale_factor": device.get("device_scale_factor"),
        "is_mobile": device.get("is_mobile", True),
        "has_touch": device.get("has_touch", True),
        "locale": "en-US",
    }
    extra_headers: dict[str, str] = {}
    if WEBVIEW_FINGERPRINT:
        # UA: WebView UA unless isolation drops it (then fall back to device UA).
        kwargs["user_agent"] = WEBVIEW_UA if WV_SEND_UA else device.get("user_agent")
        # sec-ch-ua brand: force "Android WebView" unless isolation drops it.
        if WV_SEND_SEC_CH_UA:
            extra_headers["sec-ch-ua"] = _WEBVIEW_CLIENT_HINTS["sec-ch-ua"]
        # These two are identical in the passing Chrome control, so always send.
        extra_headers["sec-ch-ua-mobile"] = _WEBVIEW_CLIENT_HINTS["sec-ch-ua-mobile"]
        extra_headers["sec-ch-ua-platform"] = _WEBVIEW_CLIENT_HINTS[
            "sec-ch-ua-platform"
        ]
        if WV_SEND_XRW:
            extra_headers["x-requested-with"] = WEBVIEW_PACKAGE
        if WV_FORCE_ORIGIN:
            extra_headers["origin"] = WV_FORCE_ORIGIN
    else:
        # A/B control: plain mobile Chrome as Playwright's device ships it.
        kwargs["user_agent"] = device.get("user_agent")
    if extra_headers:
        kwargs["extra_http_headers"] = extra_headers
    return kwargs


@contextmanager
def chromium_webview_context(state_file: Path, device: dict):
    first_login = STATE_RESET or not state_file.exists()
    if first_login:
        logger.info(
            "no usable state at {} - fresh session (log in manually)", state_file
        )
        state_file.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        # Flags that push stock Chromium closer to an Android WebView surface.
        launch_args = [
            "--disable-blink-features=AutomationControlled",
        ]
        browser = p.chromium.launch(headless=HEADLESS, args=launch_args)
        kwargs = _build_context_kwargs(device)
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
    mode = "webview" if WEBVIEW_FINGERPRINT else "chrome_control"
    ctx = TraceContext(
        "kraken_webview_android",
        user_id=mode,
        session_id=uuid.uuid4().hex[:8],
    )
    configure_logging(ctx)
    tracer = PageTracer(ctx)

    with sync_playwright() as _p:
        device = dict(_p.devices.get(DEVICE_NAME, _p.devices["Pixel 7"]))

    if WEBVIEW_FINGERPRINT:
        ua = WEBVIEW_UA if WV_SEND_UA else device.get("user_agent")
    else:
        ua = device.get("user_agent")
    logger.info(
        "engine=Chromium mode={} device={!r} viewport={} headless={}",
        mode,
        DEVICE_NAME,
        device.get("viewport"),
        HEADLESS,
    )
    logger.info("user-agent = {}", ua)
    if WEBVIEW_FINGERPRINT:
        active = {
            "sec-ch-ua(AndroidWebView)": WV_SEND_SEC_CH_UA,
            "x-requested-with": WV_SEND_XRW,
            "webview-UA": WV_SEND_UA,
        }
        logger.info("ISOLATION switches (True=WebView value sent): {}", active)
        if not all(active.values()):
            logger.warning(
                "ISOLATION RUN: one or more WebView tells DROPPED -> if tfa now "
                "returns 200, the dropped tell is the culprit."
            )
    else:
        logger.warning(
            "A/B CONTROL mode: plain mobile Chrome, NO Android-WebView headers"
        )

    # Per-fingerprint state dir so WebView vs Chrome sessions never mix.
    state_file = (
        Path(".context") / "kraken_webview_android" / mode / "001" / "state.json"
    )

    with chromium_webview_context(state_file, device) as (context, first_login):
        page = context.new_page()

        clock = SeqClock(
            meta={
                "engine": "chromium",
                "mode": mode,
                "device": DEVICE_NAME,
                "viewport": device.get("viewport"),
                "headless": HEADLESS,
                "start_url": KRAKEN_URL,
                "user_agent": ua,
                "webview_fingerprint": WEBVIEW_FINGERPRINT,
                "client_hints": _WEBVIEW_CLIENT_HINTS if WEBVIEW_FINGERPRINT else None,
                "x_requested_with": WEBVIEW_PACKAGE if WEBVIEW_FINGERPRINT else None,
                "isolation": {
                    "send_sec_ch_ua": WV_SEND_SEC_CH_UA,
                    "send_x_requested_with": WV_SEND_XRW,
                    "send_webview_ua": WV_SEND_UA,
                }
                if WEBVIEW_FINGERPRINT
                else None,
            }
        )
        recorder = NetworkRecorder(ctx.log_dir)
        snapshotter = StateSnapshotter(ctx.log_dir, context)
        attach_network_logger(page, recorder, clock)

        def capture(kind: str) -> None:
            seq = clock.next()
            parsed = urlparse(page.url)
            slug = re.sub(
                r"[^\w]+", "_", f"{parsed.netloc}{parsed.path}"
            ).strip("_")[:60]
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

        scratch_pages: list[Page] = []

        def open_scratch_tab(url: str) -> None:
            if not urlparse(url).scheme:
                url = "https://" + url
            p2 = context.new_page()  # no hooks -> unrecorded
            scratch_pages.append(p2)
            try:
                p2.goto(url, wait_until="domcontentloaded")
                logger.info("opened UNRECORDED scratch tab -> {}", url)
            except Exception as exc:  # noqa: BLE001
                logger.error("scratch tab goto failed for {}: {}", url, exc)

        def _stdin_line() -> str | None:
            try:
                if select.select([sys.stdin], [], [], 0)[0]:
                    return sys.stdin.readline().strip()
            except Exception:  # noqa: BLE001
                pass
            return None

        logger.info("navigating to {}", KRAKEN_URL)
        page.goto(KRAKEN_URL, wait_until="domcontentloaded")

        # Confirm the fingerprint actually reached the JS layer.
        fp = probe_fingerprint(page)
        logger.info("page-visible fingerprint: {}", json.dumps(fp, ensure_ascii=False))

        if first_login:
            logger.warning(
                "FIRST RUN: log in manually in the browser window "
                "(solve any captcha/2FA). Watch for the AUTH ... -> <status> "
                "lines; a 401 on .../account/settings/tfa reproduces the "
                "on-device failure. Ctrl-C to stop and persist session."
            )

        logger.info(
            "COMMANDS: type 'fp' + Enter to re-probe the fingerprint; type a "
            "URL + Enter for an UNRECORDED scratch tab; Ctrl-C to stop."
        )

        try:
            last_tick = 0.0
            while True:
                line = _stdin_line()
                if line == "fp":
                    logger.info(
                        "page-visible fingerprint: {}",
                        json.dumps(probe_fingerprint(page), ensure_ascii=False),
                    )
                elif line:
                    open_scratch_tab(line)
                now = time.monotonic()
                if now - last_tick >= DUMP_INTERVAL_S:
                    capture("tick")
                    dom = discover_dom(page)
                    logger.info(
                        "DOM discovery: {}", json.dumps(dom, ensure_ascii=False)
                    )
                    last_tick = now
                page.wait_for_timeout(200)
        except KeyboardInterrupt:
            logger.info("stopping on Ctrl-C")
        finally:
            clock.write_manifest(ctx.log_dir / "manifest.json")
            logger.info("wrote manifest -> {}", ctx.log_dir / "manifest.json")


if __name__ == "__main__":
    main()
