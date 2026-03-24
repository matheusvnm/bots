from loguru import logger
from patchright.sync_api import Page

from components.dtos import CryptoAsset, CryptoNetwork
from components.trace import PageTracer
from services.kraken.browser.interceptors import KrakenInterceptor


class KrakenDeposit:
    def __init__(
        self, page: Page, tracer: PageTracer, interceptor: KrakenInterceptor, **_
    ):
        self.page = page
        self.tracer = tracer
        self.interceptor = interceptor

    def select_asset(self) -> CryptoAsset | None:
        assets = self._fetch_assets()
        logger.info("Available assets for deposit ({} total):", len(assets))
        for i, asset in enumerate(assets, 1):
            print(f"  {i:4d}. {asset}")

        index = input("[?] Enter number or ticker to deposit: ").strip()
        if not index.isdigit():
            logger.warning("The value must be a integer")
            return None

        idx = int(index) - 1
        return assets[idx] if 0 <= idx < len(assets) else None

    def _fetch_assets(self) -> list[CryptoAsset]:
        logger.info("Intercepting deposit assets from browser API...")
        self._open_asset_modal()
        return self.interceptor.deposit_assets()

    def _open_asset_modal(self) -> None:
        """Navigate to portfolio and open the deposit crypto selection modal."""
        logger.info("Deposit step 1 — clicking portfolio link")
        self.page.click('a[href="/c/portfolio"]')
        self.page.wait_for_load_state("domcontentloaded")
        logger.info("Portfolio page loaded (url={})", self.page.url)
        self.tracer.save(self.page, "portfolio_page")

        logger.info("Deposit step 2 — waiting for #instant-btn-deposit")
        self.page.wait_for_selector("#instant-btn-deposit", timeout=60000)
        logger.info("Button visible — clicking")
        self.tracer.save(self.page, "deposit_btn_visible")
        self.page.click("#instant-btn-deposit")

        logger.info("Deposit step 3 — waiting for modal to open")
        self.page.wait_for_selector('[role="heading"]', timeout=30000)
        logger.info("Modal opened — waiting 2s for list to load")
        self.page.wait_for_timeout(2000)
        self.tracer.save(self.page, "deposit_modal_open")

    def select_network(self, asset_name: str, asset: str) -> CryptoNetwork:
        networks_to_choose = self._open_network_modal(asset_name, asset)
        if len(networks_to_choose) == 1:
            return networks_to_choose[0]

        for i, network in enumerate(networks_to_choose, 1):
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

        index = input("[?] Enter number of the network: ").strip()
        if not index.isdigit():
            logger.warning("The value must be a integer")
            return None

        idx = int(index) - 1
        return networks_to_choose[idx] if 0 <= idx < len(networks_to_choose) else None

    def _open_network_modal(self, asset_name: str, asset: str) -> list[CryptoNetwork]:
        logger.info(f"Searching for asset {asset_name} in deposit list")
        self._click_on_crypto(asset_name)
        return self.interceptor.networks(asset)

    def _click_on_crypto(self, asset_name: str) -> None:
        """Search for the asset in the deposit modal search box, then click it."""
        logger.info(f"Searching for '{asset_name}' in deposit modal")
        search_input = self.page.locator('input[placeholder="Search"]')
        search_input.fill(asset_name)
        self.page.wait_for_timeout(1000)

        el = (
            self.page.locator(".text-ds-primary.text-left")
            .filter(has_text=asset_name)
            .first
        )
        el.click()
        self.tracer.save(self.page, "deposit_crypto_selected")
        logger.info("Waiting 5s for network modal to load.")
        self.page.wait_for_timeout(5000)

    def generate_address(self, asset: str, network_name: str) -> None:
        existing_networks = self.interceptor.networks(asset)
        if len(existing_networks) > 1:
            logger.info(
                f"{len(existing_networks)} networks detected — selecting '{network_name}'"
            )
            element = self.page.locator(f'[aria-label="{network_name}"]').first
            element.click()
            self.page.wait_for_timeout(1000)
            self.tracer.save(self.page, "deposit_network_selected")

        logger.info("Clicking 'I understand' confirmation")
        self.page.wait_for_selector('span:has-text("I understand")', timeout=15000)
        self.page.click('span:has-text("I understand")')

        logger.info("Address generated successfully — waiting 2s getting information")
        self.page.wait_for_timeout(2000)
        self.tracer.save(self.page, "address_network_generated")

        addresses = self.interceptor.addresses(asset, network_name)
        if addresses:
            return addresses[-1]

        return None

    def run(self) -> None:
        """
        Deposit flow:
          1. Open modal and intercept asset list from the browser's API call.
          2. Print all assets numbered for selection.
          3. Ask the user which asset to deposit.
          4. Log the intended action (execution TBD).
        """

        selected_asset = self.select_asset()
        if not selected_asset:
            logger.warning("No asset matched — aborting")
            return

        logger.info(f"Selected for deposit: {selected_asset}")
        selected_network = self.select_network(
            selected_asset.name, selected_asset.asset
        )
        if not selected_network:
            logger.warning("No network matched — aborting")
            return

        logger.info(f"Selected for deposit: {selected_network.name}")
        selected_address = self.generate_address(
            selected_asset.asset, selected_network.name
        )

        print("Success! You deposit network and adress is the following: ")
        print(f"Asset: {selected_asset.name} ({selected_asset.asset})")
        print(f"Network: {selected_network.name}")
        print(f"Address: {selected_address.address}")
        if selected_address.tag:
            print(f"Tag: {selected_address.tag}")
