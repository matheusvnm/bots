"""
Coinbase deposit/receive flow.

Navigates through the Receive modal to list available assets,
selects one, handles network selection and warning, then retrieves
the deposit address via the clipboard copy button.

Usage (integrated into coinbase_login.py __main__):
    with authenticator.login(state_path, credentials) as page:
        deposit = CoinbaseDeposit(page, tracer)
        deposit.run()
"""

from loguru import logger
from patchright.sync_api import Page

from components.trace import PageTracer
from components.utils import human_delay


class CoinbaseDeposit:
    def __init__(self, page: Page, tracer: PageTracer):
        self.page = page
        self.tracer = tracer

    def _open_receive_modal(self) -> None:
        logger.info("Opening receive modal...")
        human_delay(self.page)
        self.page.click('[data-testid="quick-action-receive"]')
        self.page.wait_for_selector('[data-testid="search-input"]', timeout=15000)
        self.tracer.save(self.page, "receive_modal_open")
        logger.info("Receive modal open")

    def _list_assets(self) -> list[str]:
        """Read all available asset tickers directly from the asset selector DOM."""
        cells = self.page.locator(
            '[data-testid^="ReceiveAssetSelectorCell-"][data-testid$="-cell-pressable"]'
        )
        tickers = []
        for i in range(cells.count()):
            testid = cells.nth(i).get_attribute("data-testid") or ""
            ticker = testid.removeprefix("ReceiveAssetSelectorCell-").removesuffix(
                "-cell-pressable"
            )
            if ticker:
                tickers.append(ticker)
        return tickers

    def _resolve_choice(self, choice: str, assets: list[str]) -> str | None:
        if choice.isdigit():
            idx = int(choice) - 1
            return assets[idx] if 0 <= idx < len(assets) else None
        upper = choice.upper()
        return upper if upper in assets else None

    def _select_asset(self, ticker: str) -> None:
        logger.info("Selecting asset {}...", ticker)
        self.page.fill('[data-testid="search-input"]', ticker)
        self.page.wait_for_timeout(500)
        self.page.click(
            f'[data-testid="ReceiveAssetSelectorCell-{ticker}-cell-pressable"]'
        )
        self.tracer.save(self.page, f"asset_selected_{ticker}")
        logger.info("Asset {} selected", ticker)

    def _handle_network_selection(self) -> str | None:
        """Handle network selection if shown. Returns selected network name or None."""
        self.page.wait_for_selector(
            '[data-testid="step-networkSelection-active"], '
            '[data-testid="receive-redesign-copy-button"], '
            '[data-testid="network-warning-step-understand"]',
            timeout=15000,
        )
        if not self.page.locator(
            '[data-testid="step-networkSelection-active"]'
        ).is_visible():
            return None  # Single network — auto-selected

        logger.info("Network selection required — waiting for list to load...")

        # Network items have data-testid="{name}-network" (e.g. "bitcoin-network").
        # Wait until at least one appears (skeleton shows "Loading" spans while loading).
        self.page.wait_for_selector('[data-testid$="-network"]', timeout=15000)
        self.tracer.save(self.page, "network_selection")

        items = self.page.locator('[data-testid$="-network"]')
        networks: list[tuple[str, int]] = []
        for i in range(items.count()):
            name = items.nth(i).inner_text().strip().splitlines()[0]
            if name:
                networks.append((name, i))

        if not networks:
            logger.warning("No network options found — proceeding without selection")
            return None

        if len(networks) == 1:
            name, idx = networks[0]
            logger.info("Single network available: {} — auto-selecting", name)
            items.nth(idx).click()
            self.tracer.save(self.page, f"network_selected_{name}")
            return name

        logger.info("Available networks:")
        for i, (name, _) in enumerate(networks, 1):
            print(f"  {i}. {name}")

        choice = input("[?] Enter network number: ").strip()
        if not choice.isdigit():
            raise ValueError("Invalid network selection")
        idx = int(choice) - 1
        if not (0 <= idx < len(networks)):
            raise ValueError("Network index out of range")

        name, item_idx = networks[idx]
        items.nth(item_idx).click()
        self.tracer.save(self.page, f"network_selected_{name}")
        logger.info("Network {} selected", name)
        return name

    def _handle_warning(self) -> None:
        """Click 'I understand' if a network warning appears."""
        try:
            self.page.wait_for_selector(
                '[data-testid="network-warning-step-understand"]', timeout=5000
            )
            self.tracer.save(self.page, "network_warning")
            self.page.click('[data-testid="network-warning-step-understand"]')
            logger.info("Network warning acknowledged")
        except Exception:
            pass  # No warning shown for this asset/network

    def _get_address(self) -> str:
        """Click the copy button and read the deposit address from the clipboard."""
        self.page.wait_for_selector(
            '[data-testid="receive-redesign-copy-button"]', timeout=15000
        )
        self.tracer.save(self.page, "address_display")
        self.page.click('[data-testid="receive-redesign-copy-button"]')
        address = self.page.evaluate("navigator.clipboard.readText()")
        logger.info("Address retrieved: {}", address)
        return address

    def run(self) -> None:
        self._open_receive_modal()

        assets = self._list_assets()
        logger.info("Available assets ({} total):", len(assets))
        for i, ticker in enumerate(assets, 1):
            print(f"  {i:4d}. {ticker}")

        choice = input("[?] Enter number or ticker: ").strip()
        ticker = self._resolve_choice(choice, assets)
        if not ticker:
            logger.warning("Invalid selection — aborting")
            return

        self._select_asset(ticker)
        network = self._handle_network_selection()
        self._handle_warning()
        address = self._get_address()

        print("\n── Deposit info ──────────────────────────")
        print(f"  Asset:   {ticker}")
        if network:
            print(f"  Network: {network}")
        print(f"  Address: {address}")
        print("──────────────────────────────────────────")
