"""
Kraken WebKit / iOS probe.

Purpose
-------
Observe how Kraken's authenticated web flows behave inside a WebKit engine
(the same family as an iOS WKWebView), so we can learn what an iOS Connect
SDK would see and start coding an integration against it.

Unlike the Coinbase probe (which diagnoses a *known* iOS scraper against
GraphQL), this is a greenfield DISCOVERY tool for Kraken's REST/JSON site.
It runs the **WebKit** engine, an **iPhone device descriptor** (mobile UA +
viewport + touch), and on a fixed interval dumps everything the future
integration code will need to reason about:

  1. screenshots/  -> full-page PNG of the rendered page
  2. html/         -> full page HTML (for offline selector discovery)
  3. network.jsonl -> every request + response (structured, one row each)
  4. responses/    -> every JSON / XHR response body, split into its own
                      pretty-printed file (Kraken is REST/JSON, so this is
                      where the authoritative balance / history data lands)
  5. storage/      -> cookies + localStorage + sessionStorage + IndexedDB
  6. manifest.json -> seq-indexed run manifest correlating all artefacts

It also runs a GENERIC DOM discovery pass each tick (no hardcoded selectors):
a data-testid inventory, candidate table/row elements, and visible text
samples -- to help us DISCOVER the selectors we will later code against.

Navigation is PASSIVE: you drive the browser (log in, solve captcha/2FA,
click through balances / history / deposit / withdraw), and the probe
auto-captures on every page load and on each tick.

Run (headful so you can log in / solve captcha / 2FA):
    cd /Users/smmarques/Github/scrapping/bots
    uv run python kraken_webkit_ios_probe.py

First run logs you in interactively; session is persisted to
.context/kraken_webkit/user/001/state.json and reused next time.

Flags via env:
    KRAKEN_URL       start url (default https://www.kraken.com/c)
    KRAKEN_HEADLESS  "1" to run headless (default headful)
    KRAKEN_DEVICE    playwright device name (default "iPhone 14 Pro")
    KRAKEN_INTERVAL  seconds between dumps (default 2)
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
KRAKEN_URL = os.environ.get("KRAKEN_URL", "https://www.kraken.com/c")
HEADLESS = os.environ.get("KRAKEN_HEADLESS", "0") == "1"
DEVICE_NAME = os.environ.get("KRAKEN_DEVICE", "iPhone 14 Pro")
DUMP_INTERVAL_S = float(os.environ.get("KRAKEN_INTERVAL", "2"))

# Response bodies larger than this are recorded as a placeholder, not stored.
_MAX_BODY_BYTES = 5_000_000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# tracing context + logging (mirrors coinbase_webkit_ios_probe.py conventions)
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

  // Inventory of data-testid values present on the page (deduped, capped).
  const tids = new Set();
  document.querySelectorAll('[data-testid]').forEach((el) => {
    const v = el.getAttribute('data-testid');
    if (v) tids.add(v);
  });
  out.dataTestIds = Array.from(tids).slice(0, 100);

  // Candidate tabular structures the balance/history views likely use.
  const countSel = (sel) => document.querySelectorAll(sel).length;
  out.counts = {
    tables: countSel('table'),
    tableRows: countSel('tr'),
    listItems: countSel('li'),
    roleRows: countSel('[role="row"]'),
    roleGrid: countSel('[role="grid"], [role="table"]'),
  };

  // Sample visible text (first ~40 non-empty short text nodes) so we can eyeball
  // where amounts/asset names render without opening the full HTML dump.
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


def discover_dom(page: Page) -> dict:
    try:
        return page.evaluate(_DOM_DISCOVERY_JS)
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
    ctx = TraceContext("kraken_webkit", user_id="001", session_id=uuid.uuid4().hex[:8])
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

    state_file = Path(".context") / "kraken_webkit" / "user" / "001" / "state.json"

    with webkit_context(state_file, device) as (context, first_login):
        page = context.new_page()

        clock = SeqClock(
            meta={
                "engine": "webkit",
                "device": DEVICE_NAME,
                "viewport": device.get("viewport"),
                "headless": HEADLESS,
                "start_url": KRAKEN_URL,
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

        # Scratch tabs: created without any hooks, so they are NOT recorded.
        # They share the logged-in context (cookies/storage) but no network,
        # screenshot, storage or DOM capture runs against them.
        scratch_pages: list[Page] = []

        def open_scratch_tab(url: str) -> None:
            if not urlparse(url).scheme:
                url = "https://" + url
            p2 = context.new_page()  # no attach_network_logger / no load hook
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

        if first_login:
            logger.warning(
                "FIRST RUN: log in manually in the browser window "
                "(solve any captcha/2FA). Probing continues meanwhile; "
                "Ctrl-C to stop and persist session."
            )

        logger.info(
            "COMMANDS: type a URL + Enter to open an UNRECORDED scratch tab; "
            "Ctrl-C to stop and persist session."
        )

        try:
            last_tick = 0.0
            while True:
                line = _stdin_line()
                if line:
                    open_scratch_tab(line)
                now = time.monotonic()
                if now - last_tick >= DUMP_INTERVAL_S:
                    capture("tick")
                    dom = discover_dom(page)
                    logger.info(
                        "DOM discovery: {}", json.dumps(dom, ensure_ascii=False)
                    )
                    last_tick = now
                page.wait_for_timeout(200)  # yield to browser + poll cadence
        except KeyboardInterrupt:
            logger.info("stopping on Ctrl-C")
        finally:
            clock.write_manifest(ctx.log_dir / "manifest.json")
            logger.info("wrote manifest -> {}", ctx.log_dir / "manifest.json")


if __name__ == "__main__":
    main()
