from dataclasses import asdict

from loguru import logger
from patchright.sync_api import Page

from components.dtos import CryptoAsset, CryptoNetwork
from components.trace import PageTracer
from services.kraken.interceptors import (
    AccountBalanceInterceptor,
    AssetListingInterceptor,
    MarketCapInterceptor,
    NetworkInterceptor,
)


class KrakenDeposit:
    def __init__(
        self,
        tracer: PageTracer,
        asset_listing_interceptor: AssetListingInterceptor,
        account_balance_interceptor: AccountBalanceInterceptor,
        market_cap_interceptor: MarketCapInterceptor,
        network_interceptor: NetworkInterceptor,
        **_,
    ):
        self.tracer = tracer
        self.asset_listing_interceptor = asset_listing_interceptor
        self.account_balance_interceptor = account_balance_interceptor
        self.market_cap_interceptor = market_cap_interceptor
        self.network_interceptor = network_interceptor

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

    def _generate_address(self, asset: CryptoAsset, network: CryptoNetwork, page: Page) -> None:
        # Instructions
        # Find the element where the name equals to asset.name (scroll the tab if needed).
        # Click on it.


        if asset.networks > 1:
            # 1. Select the network wit the same name as network.name (scroll the tab if needed).
            # 2. Click on it.
            pass
        
        # 3. The modal load confirmation "I understand" as a span appear, just click on it.
        logger.info("Address generated succesfully — waiting 2s getting information")
        page.wait_for_timeout(2000)
        self.tracer.save(page, "address_network_generated")


    def _fetch_assets(self, page: Page) -> list[CryptoAsset]:
        logger.info("Intercepting deposit assets from browser API...")
        self._open_crypto_modal(page)

        assets = []
        for item in self.asset_listing_interceptor.get():
            name = item["name"]
            asset = item["asset"]
            balance = self.account_balance_interceptor.get(asset)
            market_cap_rank = self.market_cap_interceptor.get(asset)
            networks = self.network_interceptor.get(asset)

            cripto = CryptoAsset(
                name=name,
                asset=asset,
                balance=balance,
                market_cap_rank=market_cap_rank,
                networks=networks,
            )
            assets.append(cripto)

        assets.sort()

        logger.info("Intercepted {} enabled crypto assets for deposit", len(assets))
        return assets

    def _find_asset(self, assets: list[CryptoAsset], query: str) -> CryptoAsset | None:
        """Find an asset by 1-based index, ticker, or partial name (case-insensitive)."""
        if query.isdigit():
            idx = int(query) - 1
            return assets[idx] if 0 <= idx < len(assets) else None

        q = query.upper()
        for asset in assets:
            if asset.asset and asset.asset.upper() == q:
                return asset

        q_lower = query.lower()
        for asset in assets:
            if q_lower in asset.name.lower():
                return asset

        return None
    
    def _find_network(self, networks: list[CryptoNetwork], query: int) -> CryptoAsset | None:
        """Find an asset by 1-based index."""
        idx = int(query) - 1
        return networks[idx] if 0 <= idx < len(networks) else None


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
            print(f"  {i:4d}. {asset}")

        asset_choice = input("[?] Enter number or ticker to deposit: ").strip()
        selected_asset = self._find_asset(assets, asset_choice)

        if not selected_asset:
            logger.warning("No asset matched '{}' — aborting", asset_choice)
            return 

        
        logger.info(f"Selected for deposit: {selected_asset}")

        selected_network = selected_asset.networks[0]
        if len(selected_asset.networks) > 1:
            logger.info(f"More than network detected we must choose.")
            for i, network in enumerate(selected_asset.networks, 1):
                print(f"  {i:4d}. {network.name}")
                print(f"        Required Confirmation:  {network.confirmations}")
                print(f"        Confirmation Time: {network.confirmation_time}")

                if network.minimum_amount is not None:
                    print(f"        Minimum Transaction: {network.minimum_amount}")
                
                if network.maximum_amount is not None:
                    print(f"        Maximum Transaction: {network.maximum_amount}")

                if network.fee is not None:
                    fee = network.fee.fee or network.fee.fee_percentage
                    if network.fee.fee:
                        print(f"        Fee: {network.fee.fee}")
                    
                    if network.fee.fee_percentage:
                        print(f"        Fee Percentage: {fee}%")


            network_choice = input("[?] Enter number or name of the network: ").strip()
            selected_network = self._find_network(selected_asset.networks, network_choice)

        if not selected_network:
            logger.warning("No network matched '{}' — aborting", asset_choice)
            return 

        if not selected_network.addresses:
            logger.debug("We did not found a valid address.")
            self._generate_address(selected_asset, selected_network, page)

        address = selected_network.addresses[-1]
        print("Success! You deposit network and adress is the following: ")
        print(f"Network: {selected_network.name}")
        print(f"Address: {address.address}")
        if address.tag:
            print(f"Tag: {address.tag}")
        


        

