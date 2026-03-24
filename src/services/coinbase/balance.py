"""
Coinbase balance scraping flow.

Navigates through the dashboard, crypto, and cash pages to collect
all held asset balances. For cash assets missing native quantities,
visits the individual asset detail page at /price/<slug>.

Usage:
    just run coinbase balance --user 001
"""

import re

from loguru import logger
from patchright.sync_api import Page

from components.trace import PageTracer
from services.coinbase.constants import CoinbasePages

_SYMBOL_TO_CODE: dict[str, str] = {
    "R$": "BRL",
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "A$": "AUD",
    "C$": "CAD",
    "₹": "INR",
    "CHF": "CHF",
    "kr": "SEK",
}


class CoinbaseBalance:
    def __init__(self, page: Page, tracer: PageTracer, **_):
        self.page = page
        self.tracer = tracer
        self._currency: str = ""

    # ── helpers ──────────────────────────────────────────────

    def _detect_currency(self) -> str:
        """Extract the account's fiat currency ISO code from the page.

        Uses the buy-currency-input aria-label (e.g. "Comprar 0 BRL em
        Bitcoin") which contains the 3-letter code between a digit and
        a word boundary.  Falls back to the crypto-balance-header
        data-value symbol prefix when the buy widget is absent.
        """
        buy_input = self.page.locator('[data-testid="buy-currency-input"]')
        if buy_input.count() > 0:
            label = buy_input.get_attribute("aria-label") or ""
            match = re.search(r"\d+\s+([A-Z]{3})\s", label)
            if match:
                code = match.group(1)
                logger.info("Detected account currency: {}", code)
                return code

        # Fallback: parse the symbol from the balance header data-value.
        header = self.page.locator('[data-testid="crypto-balance-header"]')
        if header.count() > 0:
            data_val = (
                header.locator("div[data-value]").first.get_attribute("data-value")
                or ""
            )
            raw = data_val.replace("\u00a0", " ").strip()
            sym_match = re.match(r"^([^\d]+)", raw)
            if sym_match:
                symbol = sym_match.group(1).strip()
                code = _SYMBOL_TO_CODE.get(symbol, symbol)
                logger.info("Detected account currency (from symbol): {}", code)
                return code

        logger.warning("Could not detect currency — defaulting to USD")
        return "USD"

    def _parse_currency_value(self, raw: str) -> str:
        """Strip currency symbol and normalise to plain decimal.

        'R$\\u00a013,74'  -> '13.74'
        'R$ 370.692,63'   -> '370692.63'
        """
        text = raw.replace("\u00a0", " ").strip()
        text = re.sub(r"^[^\d]*", "", text)
        text = text.replace(".", "")
        text = text.replace(",", ".")
        return text.strip()

    def _parse_quantity(self, raw: str) -> tuple[str, str]:
        """Split '0,00003707 BTC' into ('0.00003707', 'BTC')."""
        parts = raw.strip().split()
        value = parts[0].replace(".", "").replace(",", ".")
        ticker = parts[1] if len(parts) > 1 else ""
        return value, ticker

    # ── page scrapers ───────────────────────────────────────

    def _scrape_crypto_assets(self) -> dict:
        """Navigate to /crypto and scrape all held crypto asset rows."""
        logger.info("Navigating to crypto page...")
        self.page.goto(CoinbasePages.CRYPTO, wait_until="domcontentloaded")
        self.page.locator('[data-testid="crypto-balance-header"]').wait_for(
            timeout=15000
        )
        self.tracer.save(self.page, "crypto_page")

        # Detect currency while on this page (buy widget is present).
        self._currency = self._detect_currency()

        rows = self.page.locator('[data-testid^="account-table-row-for-"]')
        count = rows.count()
        logger.info("Found {} crypto asset(s)", count)

        assets: dict = {}
        for i in range(count):
            row = rows.nth(i)

            testid = row.get_attribute("data-testid") or ""
            ticker = testid.removeprefix("account-table-row-for-").upper()

            balance_cell = row.locator('[data-testid^="balance-cell-for-"]')

            fiat_raw = (
                balance_cell.locator('div[class*="noWrapCss"]').first.text_content()
                or ""
            )
            qty_raw = (
                balance_cell.locator('div[class*="fgMuted"]').first.text_content() or ""
            )

            currency_value = self._parse_currency_value(fiat_raw)
            value, _ = self._parse_quantity(qty_raw)

            assets[ticker] = {
                "currency": self._currency,
                "value": value,
                "currency_value": currency_value,
            }
            logger.debug(
                "Crypto asset: {} = {} ({} {})",
                ticker,
                value,
                self._currency,
                currency_value,
            )

        return assets

    def _scrape_cash_assets(self) -> dict:
        """Navigate to the cash sub-page via the dashboard and scrape rows.

        The /cash URL redirects, so we return to /home and click through
        the balance-breakdown cell instead.
        """
        logger.info("Navigating to dashboard for cash page access...")
        self.page.goto(CoinbasePages.DASHBOARD, wait_until="domcontentloaded")
        self.page.locator('[data-testid="balance-breakdown"]').wait_for(timeout=15000)

        logger.info("Clicking into cash sub-page...")
        self.page.locator('[data-testid="cash-balance-cell-cell-pressable"]').click()
        self.page.locator('[data-testid="cash-balance-header"]').wait_for(timeout=15000)
        self.tracer.save(self.page, "cash_page")

        rows = self.page.locator('[data-testid^="cash-table-row-for-"]')
        count = rows.count()
        logger.info("Found {} cash asset(s)", count)

        # First pass: collect non-fiat tickers and fiat values from the table.
        # Fiat rows (e.g. Real/BRL) use an icon glyph with a data-testid
        # ending in "-icon", while stablecoins use a remote <img>.
        # We skip fiat rows entirely — they are not crypto assets.
        # We must collect everything before navigating away to detail pages.
        raw_entries: list[tuple[str, str]] = []
        for i in range(count):
            row = rows.nth(i)

            is_fiat = row.locator('[data-testid$="-icon"]').count() > 0
            if is_fiat:
                testid = row.get_attribute("data-testid") or ""
                name = testid.removeprefix("cash-table-row-for-")
                logger.debug("Skipping fiat currency: {}", name)
                continue

            testid = row.get_attribute("data-testid") or ""
            ticker = testid.removeprefix("cash-table-row-for-")

            fiat_raw = (
                row.locator(
                    '[data-testid="cash-total-balance-cell"] div[class*="body-"]'
                ).first.text_content()
                or ""
            )
            currency_value = self._parse_currency_value(fiat_raw)
            raw_entries.append((ticker, currency_value))

        # Second pass: resolve native quantities (may navigate away).
        assets: dict = {}
        for ticker, currency_value in raw_entries:
            value = self._get_cash_asset_quantity(ticker)

            assets[ticker] = {
                "currency": self._currency,
                "value": value,
                "currency_value": currency_value,
            }
            logger.debug(
                "Cash asset: {} = {} ({} {})",
                ticker,
                value,
                self._currency,
                currency_value,
            )

        return assets

    def _get_cash_asset_quantity(self, ticker: str) -> str:
        """Visit /price/<slug> to read the native quantity for a cash asset."""
        slug = ticker.lower()
        url = f"{CoinbasePages.PRICE}/{slug}"
        logger.info("Fetching native quantity for {} at {}", ticker, url)

        self.page.goto(url, wait_until="domcontentloaded")
        self.page.locator('[data-testid="adp-total-balance"]').wait_for(timeout=15000)
        self.tracer.save(self.page, f"asset_detail_{slug}")

        qty_el = self.page.locator(
            '[data-testid="balance-section-content"] p[class*="fgMuted"]'
        ).first
        qty_raw = qty_el.text_content() or ""
        value, _ = self._parse_quantity(qty_raw)
        logger.debug("{} native quantity: {}", ticker, value)
        return value

    # ── orchestrator ────────────────────────────────────────

    def run(self) -> None:
        balances: dict = {}

        crypto = self._scrape_crypto_assets()
        balances.update(crypto)

        cash = self._scrape_cash_assets()
        balances.update(cash)

        logger.info("Balance scraping complete — {} asset(s) found", len(balances))

        print("\n── Balance info ──────────────────────────")
        for ticker, info in balances.items():
            print(f"  Asset:          {ticker}")
            print(f"  Currency:       {info['currency']}")
            print(f"  Value:          {info['value']}")
            print(f"  Currency value: {info['currency_value']}")
            print("──────────────────────────────────────────")
