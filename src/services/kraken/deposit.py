from loguru import logger
from patchright.sync_api import Page

from components.dtos import CryptoAsset
from components.trace import PageTracer
from services.kraken.interceptors import AccountBalanceInterceptor, AssetListingInterceptor, MarketCapInterceptor


class KrakenDeposit:

    def __init__(
        self,
        tracer: PageTracer,
        asset_listing_interceptor: AssetListingInterceptor,
        account_balance_interceptor: AccountBalanceInterceptor,
        market_cap_interceptor: MarketCapInterceptor,
        **_,
    ):
        self.tracer = tracer
        self.asset_listing_interceptor = asset_listing_interceptor
        self.account_balance_interceptor = account_balance_interceptor
        self.market_cap_interceptor = market_cap_interceptor

    def _open_crypto_modal(self, page: Page) -> None:
        """Navigate to portfolio and open the deposit crypto selection modal."""
        logger.info("Deposit step 1 — clicking portfolio link")
        page.click('a[href="/c/portfolio"]')
        page.wait_for_load_state("domcontentloaded")
        logger.info("Portfolio page loaded (url={})", page.url)
        self.tracer.save(page, "portfolio_page")

        logger.info("Deposit step 2 — waiting for #instant-btn-deposit")
        page.wait_for_selector("#instant-btn-deposit", timeout=60000)
        logger.info("Button visible — clicking")
        self.tracer.save(page, "deposit_btn_visible")
        page.click("#instant-btn-deposit")

        logger.info("Deposit step 3 — waiting for modal to open")
        page.wait_for_selector('[role="heading"]', timeout=30000)
        logger.info("Modal open")
        self.tracer.save(page, "deposit_modal_open")

        logger.info("Deposit step 4 — clicking crypto tab")
        page.click('[role="tab"][id="crypto"]')
        logger.info("Crypto tab clicked — waiting 2s for list to load")
        page.wait_for_timeout(2000)
        self.tracer.save(page, "deposit_crypto_tab_loaded")

    def _fetch_assets(self, page: Page) -> list[CryptoAsset]:
        logger.info("Intercepting deposit assets from browser API...")
        self._open_crypto_modal(page)

        balance_by_ticker = {b["asset"]: b for b in self.account_balance_interceptor.get()}

        assets = []
        for item in self.asset_listing_interceptor.get():
            cripto = CryptoAsset(
                name=item["name"],
                short_name=item["asset"],
                value=balance_by_ticker.get(item["asset"], {}).get("balance"),
                usd_value=balance_by_ticker.get(item["asset"], {}).get("quote_balance"),
            )
            assets.append(cripto)

        assets.sort(key=lambda a: (
            0 if float(a.usd_value or 0) > 0 else 1,
            -float(a.usd_value or 0),
            self.market_cap_interceptor.rank(a.short_name),
        ))

        logger.info("Intercepted {} enabled crypto assets for deposit", len(assets))
        return assets

    def _find_asset(self, assets: list[CryptoAsset], query: str) -> CryptoAsset | None:
        """Find an asset by 1-based index, ticker, or partial name (case-insensitive)."""
        if query.isdigit():
            idx = int(query) - 1
            return assets[idx] if 0 <= idx < len(assets) else None

        q = query.upper()
        for asset in assets:
            if asset.short_name and asset.short_name.upper() == q:
                return asset

        q_lower = query.lower()
        for asset in assets:
            if q_lower in asset.name.lower():
                return asset

        return None

    def run(self, page: Page, **_) -> None:
        """
        Deposit flow:
          1. Open modal and intercept asset list from the browser's API call.
          2. Print all assets numbered for selection.
          3. Ask the user which asset to deposit.
          4. Log the intended action (execution TBD).
        """
        assets = self._fetch_assets(page)

        logger.info("Available assets for deposit ({} total):", len(assets))
        for i, asset in enumerate(assets, 1):
            print(f"  {i:4d}. {asset.name} ({asset.short_name or '—'})")

        choice = input("[?] Enter number or ticker to deposit: ").strip()
        selected = self._find_asset(assets, choice)

        if selected:
            logger.info(
                "Selected for deposit: {} ({})",
                selected.name, selected.short_name or "—",
            )
            logger.info("TODO: complete deposit execution for {} — not yet implemented", selected.name)
        else:
            logger.warning("No asset matched '{}' — aborting", choice)
