from loguru import logger
from patchright.sync_api import Page

from components.dtos import CryptoAsset
from components.trace import PageTracer
from services.kraken.interceptors import KrakenInterceptor


class KrakenWithdraw:
    def __init__(self, tracer: PageTracer, interceptor: KrakenInterceptor, **_):
        self.tracer = tracer
        self.interceptor = interceptor

    def _open_crypto_modal(self, page: Page) -> None:
        """Navigate to portfolio and open the withdraw crypto selection modal."""
        logger.info("Withdraw step 1 — clicking portfolio link")
        page.click('a[href="/c/portfolio"]')
        page.wait_for_load_state("domcontentloaded")
        logger.info("Portfolio page loaded (url={})", page.url)
        self.tracer.save(page, "portfolio_page")

        logger.info("Withdraw step 2 — waiting for #instant-btn-withdraw")
        page.wait_for_selector("#instant-btn-withdraw", timeout=60000)
        logger.info("Button visible — clicking")
        self.tracer.save(page, "withdraw_btn_visible")
        page.click("#instant-btn-withdraw")

        logger.info("Withdraw step 3 — waiting for modal to open")
        page.wait_for_selector('[role="heading"]', timeout=30000)
        logger.info("Modal open")
        self.tracer.save(page, "withdraw_modal_open")

        logger.info("Withdraw step 4 — clicking crypto tab")
        page.click('[role="tab"][id="crypto"]')
        logger.info("Crypto tab clicked — waiting 5 s for list to load")
        page.wait_for_timeout(5000)
        self.tracer.save(page, "withdraw_crypto_tab_loaded")

    def _fetch_assets(self, page: Page) -> list[CryptoAsset]:
        logger.info("Intercepting withdraw balances from browser API...")
        self._open_crypto_modal(page)

        assets = self.interceptor.withdraw_assets()

        logger.info("Intercepted {} non-zero crypto assets for withdraw", len(assets))
        return assets

    def _find_asset(self, assets: list[CryptoAsset], query: str) -> CryptoAsset | None:
        """Find an asset by 1-based index, ticker, or partial name (case-insensitive)."""
        if query.isdigit():
            idx = int(query) - 1
            return assets[idx] if 0 <= idx < len(assets) else None

        q = query.upper()
        for asset in assets:
            if asset.asset.upper() == q:
                return asset

        q_lower = query.lower()
        for asset in assets:
            if q_lower in asset.name.lower():
                return asset

        return None

    def run(self, page: Page, **_) -> None:
        """
        Withdraw flow:
          1. Scrape assets with non-zero balance (stops at first zero-balance row).
          2. Print available assets with their current balances.
          3. Ask the user which asset to withdraw.
          4. Log the intended action (execution TBD).
        """
        assets = self._fetch_assets(page)

        if not assets:
            logger.warning(
                "No assets with non-zero balance found — nothing to withdraw"
            )
            return

        logger.info("Available assets for withdrawal ({} total):", len(assets))
        for i, asset in enumerate(assets, 1):
            bal = asset.balance
            print(
                f"  {i:4d}. {asset.name} ({asset.asset}) — {bal.value if bal else '—'} ({bal.usd_value if bal else '—'} USD)"
            )

        choice = input("[?] Enter number or ticker to withdraw: ").strip()
        selected = self._find_asset(assets, choice)

        if selected:
            bal = selected.balance
            logger.info(
                "Selected for withdrawal: {} ({}) — balance: {} ({} USD)",
                selected.name,
                selected.asset,
                bal.value if bal else "—",
                bal.usd_value if bal else "—",
            )
            logger.info(
                "TODO: complete withdraw execution for {} — not yet implemented",
                selected.name,
            )
        else:
            logger.warning("No asset matched '{}' — aborting", choice)
