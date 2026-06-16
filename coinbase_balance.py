"""Coinbase getBalance via headed WebKit GraphQL interception.

Drives a real Playwright WebKit session at an iPhone profile, intercepts the
page's own CryptoQuery/CashQuery responses, parses them into the extension's
AssetBalance[] contract, and prints a ZeroAuthResponse JSON envelope.

Faithful port of scraper-browser-extensions/src/platforms/coinbase/operations/
{balance-resolver,get-crypto,get-cash}.ts.
"""

from __future__ import annotations

import enum
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

# ─── Model ──────────────────────────────────────────────────────────────────


@dataclass
class AssetBalance:
    key: str
    label: str
    amount: str
    notional: str
    currency: Optional[str]
    totalStakedPercent: Optional[str]
    precision: Optional[int]
    extractedAt: str


class ParseStatus(enum.Enum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


# ─── Errors (string contract, matching the extension) ─────────────────────────

CHALLENGE_PRESENT = "CHALLENGE_PRESENT"
CHALLENGE_UNSOLVED = "CHALLENGE_UNSOLVED"
NOT_LOGGED_IN = "NOT_LOGGED_IN"


def balances_indeterminate(operation: str) -> str:
    return f"BALANCES_INDETERMINATE: {operation} — could not load a complete response"


# ─── Pure parser ──────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _has_edges(field: Any) -> bool:
    return isinstance(field, dict) and isinstance(field.get("edges"), list)


def _cash_currency(viewer: dict[str, Any]) -> Optional[str]:
    """Global display currency for cash rows (CashQuery carries no per-node currency).

    The whole account uses one display currency, so every cash row inherits it.
    """
    summary = (viewer.get("netPerformance") or {}).get("summary") or {}
    deposited = summary.get("deposited") or {}
    currency = deposited.get("currency")
    if currency:
        return currency
    fiat_accounts = viewer.get("fiatAccounts")
    if isinstance(fiat_accounts, list) and fiat_accounts:
        first = fiat_accounts[0]
        if isinstance(first, dict):
            return (first.get("availableBalance") or {}).get("currency")
    return None


def _staked_percent(node: dict[str, Any]) -> Optional[str]:
    asset = node.get("asset")
    summary = None
    if isinstance(asset, dict):
        staking = asset.get("staking")
        if isinstance(staking, dict):
            summary = staking.get("summary")
    raw = summary.get("totalStakedPercent") if isinstance(summary, dict) else None
    if raw is None:
        return None
    try:
        return str(float(raw) * 100)
    except (TypeError, ValueError):
        return None


def _parse_connection(
    data: dict[str, Any],
    field: str,
    extras: Callable[
        [dict[str, Any], dict[str, Any]], tuple[Optional[str], Optional[str]]
    ],
) -> tuple[ParseStatus, list[AssetBalance]]:
    viewer = (data or {}).get("data", {}).get("viewer")
    if not isinstance(viewer, dict):
        return ParseStatus.INCOMPLETE, []

    connection = viewer.get(field)
    if not _has_edges(connection):
        return ParseStatus.INCOMPLETE, []

    balances: list[AssetBalance] = []
    for edge in connection["edges"]:
        node = edge.get("node") if isinstance(edge, dict) else None
        if not isinstance(node, dict):
            continue
        outer_asset = node.get("asset")
        asset = outer_asset.get("asset") if isinstance(outer_asset, dict) else None
        if not isinstance(asset, dict):
            continue  # skips TiersCurrency / malformed edges

        amount = (node.get("totalBalanceCrypto") or {}).get("amount") or "0"
        notional = (node.get("totalBalanceFiat") or {}).get("amount") or "0"
        try:
            notional_zero = float(notional) == 0
        except (TypeError, ValueError):
            notional_zero = False
        if amount == "0" and notional_zero:
            continue

        currency, staked = extras(node, viewer)
        balances.append(
            AssetBalance(
                key=asset.get("displaySymbol") or asset.get("platformName") or "",
                label=asset.get("name") or "",
                amount=amount,
                notional=notional,
                currency=currency,
                totalStakedPercent=staked,
                precision=None,
                extractedAt=_now_iso(),
            )
        )
    return ParseStatus.COMPLETE, balances


def parse_crypto(data: dict[str, Any]) -> tuple[ParseStatus, list[AssetBalance]]:
    def extras(node: dict[str, Any], viewer: dict[str, Any]):
        currency = (
            ((viewer.get("oneDayCryptoPerformance") or {}).get("returns") or {})
            .get("unrealized", {})
            .get("value", {})
            .get("currency")
        ) or "USD"
        return currency, _staked_percent(node)

    return _parse_connection(data, "cryptoAssets", extras)


def parse_cash(data: dict[str, Any]) -> tuple[ParseStatus, list[AssetBalance]]:
    def extras(node: dict[str, Any], viewer: dict[str, Any]):
        return _cash_currency(viewer), None

    return _parse_connection(data, "cashAssets", extras)


# ─── Response envelope (ZeroAuthResponse shape) ───────────────────────────────


def success_envelope(balances: list[AssetBalance]) -> dict[str, Any]:
    return {
        "success": True,
        "data": [asdict(b) for b in balances],
        "error": None,
        "retryable": False,
    }


def error_envelope(error: str, retryable: bool = False) -> dict[str, Any]:
    return {
        "success": False,
        "data": None,
        "error": error,
        "retryable": retryable,
    }


# ─── Interceptor ──────────────────────────────────────────────────────────────

import json  # noqa: E402
import time  # noqa: E402
from urllib.parse import parse_qs, urlparse  # noqa: E402

from probe.multipart import fold_multipart  # noqa: E402

_BALANCE_OPS = ("CryptoQuery", "CashQuery")


def _operation_name(url: str, body: Optional[str]) -> Optional[str]:
    qs = parse_qs(urlparse(url).query)
    if qs.get("operationName"):
        return qs["operationName"][0]
    if body:
        try:
            parsed = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            return None
        if isinstance(parsed, dict) and isinstance(parsed.get("operationName"), str):
            return parsed["operationName"]
    return None


def _decode_graphql_body(body: str, content_type: str) -> Optional[dict[str, Any]]:
    """Decode a GraphQL body (plain JSON or multipart/@defer); None on failure."""
    if body is None:
        return None
    try:
        if "multipart" in (content_type or "").lower():
            return fold_multipart(body)
        return json.loads(body)
    except Exception:  # noqa: BLE001 — any decode failure means "not usable"
        return None


class BalanceInterceptor:
    """Stashes folded CryptoQuery/CashQuery responses; tracks Cloudflare challenges."""

    def __init__(self) -> None:
        self.responses: dict[str, dict[str, Any]] = {}
        self.challenge_seen = False

    def attach(self, page: Any) -> None:
        page.on("response", self._on_response)

    def _on_response(self, response: Any) -> None:
        url = response.url
        if response.status == 429 or "cf-mitigated" in (response.headers or {}):
            self.challenge_seen = True
        if "/graphql" not in urlparse(url).path:
            return
        op = _operation_name(url, None)
        if op not in _BALANCE_OPS:
            return
        try:
            raw = response.body()
        except Exception:  # noqa: BLE001
            return
        body = raw.decode("utf-8", errors="replace") if raw is not None else None
        ctype = (response.headers or {}).get("content-type", "")
        folded = _decode_graphql_body(body, ctype) if body else None
        if folded is not None:
            self.responses[op] = folded


# ─── Session ──────────────────────────────────────────────────────────────────

from contextlib import contextmanager  # noqa: E402
from pathlib import Path  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402

COINBASE_BASE = "https://www.coinbase.com"
DEFAULT_STATE = Path(".context") / "coinbase_webkit" / "user" / "001" / "state.json"
DEVICE_NAME = "iPhone 14 Pro"


@contextmanager
def webkit_session(state_file: Path, headless: bool):
    """Yield a warm WebKit page at iPhone profile; persist storage state on exit."""
    first_login = not state_file.exists()
    if first_login:
        state_file.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        device = dict(p.devices.get(DEVICE_NAME, p.devices["iPhone 14 Pro"]))
        browser = p.webkit.launch(headless=headless)
        kwargs = dict(device)
        if not first_login:
            kwargs["storage_state"] = str(state_file)
        context = browser.new_context(**kwargs)
        page = context.new_page()
        try:
            yield page, first_login
        finally:
            try:
                context.storage_state(path=str(state_file))
            except Exception:  # noqa: BLE001
                pass
            browser.close()


# ─── Orchestration ────────────────────────────────────────────────────────────

import sys  # noqa: E402

_STEPS = (
    ("CryptoQuery", f"{COINBASE_BASE}/crypto", parse_crypto),
    ("CashQuery", f"{COINBASE_BASE}/cash", parse_cash),
)


def _on_login_page(url: str) -> bool:
    return "login.coinbase.com" in url


def _wait_for_op(page, interceptor, op, timeout_s) -> bool:
    """Pump the sync event loop until op's response is captured or timeout."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if op in interceptor.responses:
            return True
        page.wait_for_timeout(200)
    return op in interceptor.responses


def _gather_step(page, interceptor, op, url, parse, timeout_s):
    """Navigate, optionally pause for manual challenge solve, wait, parse.

    Returns list[AssetBalance] or raises ValueError carrying an error string.
    """
    page.goto(url, wait_until="domcontentloaded")
    if _on_login_page(page.url):
        raise ValueError(NOT_LOGGED_IN)

    captured = _wait_for_op(page, interceptor, op, timeout_s)

    if not captured and interceptor.challenge_seen:
        print(
            f"[challenge] Cloudflare challenge detected before {op}. "
            "Solve it in the browser window, then press Enter here to continue...",
            file=sys.stderr,
        )
        try:
            input()
        except EOFError:
            print("[challenge] stdin not interactive; cannot pause.", file=sys.stderr)
        captured = _wait_for_op(page, interceptor, op, timeout_s)
        if not captured:
            raise ValueError(CHALLENGE_UNSOLVED)

    folded = interceptor.responses.get(op)
    if folded is None:
        raise ValueError(balances_indeterminate(op))

    status, balances = parse(folded)
    if status == ParseStatus.INCOMPLETE:
        raise ValueError(balances_indeterminate(op))
    return balances


def get_balance(page, interceptor, timeout_s: float) -> dict[str, Any]:
    """Run the two-step composite and return a ZeroAuthResponse dict."""
    try:
        all_balances: list[AssetBalance] = []
        for op, url, parse in _STEPS:
            all_balances += _gather_step(page, interceptor, op, url, parse, timeout_s)
        return success_envelope(all_balances)
    except ValueError as exc:
        msg = str(exc)
        retryable = msg in (CHALLENGE_UNSOLVED,) or msg.startswith(
            "BALANCES_INDETERMINATE"
        )
        return error_envelope(msg, retryable=retryable)
    except Exception as exc:  # noqa: BLE001
        return error_envelope(str(exc), retryable=False)


# ─── CLI ────────────────────────────────────────────────────────────────────

import argparse  # noqa: E402


def run(timeout_s: float, state_file: Path, headless: bool) -> dict[str, Any]:
    """Open a session, gather balances, return the ZeroAuthResponse dict."""
    try:
        with webkit_session(state_file, headless) as (page, _first_login):
            interceptor = BalanceInterceptor()
            interceptor.attach(page)
            return get_balance(page, interceptor, timeout_s)
    except Exception as exc:  # noqa: BLE001
        return error_envelope(str(exc), retryable=False)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Coinbase getBalance via WebKit interception"
    )
    parser.add_argument(
        "--timeout", type=float, default=8.0, help="per-operation wait seconds"
    )
    parser.add_argument(
        "--state", type=Path, default=DEFAULT_STATE, help="storage-state path"
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="run headless (challenges won't auto-solve)",
    )
    parser.add_argument("--compact", action="store_true", help="compact JSON output")
    args = parser.parse_args(argv)

    env = run(timeout_s=args.timeout, state_file=args.state, headless=args.headless)
    indent = None if args.compact else 2
    print(json.dumps(env, indent=indent, ensure_ascii=False))
    return 0 if env["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
