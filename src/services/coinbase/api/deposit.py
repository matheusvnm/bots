"""
Coinbase API deposit action.

Fetches deposit-eligible crypto assets and their supported networks
from the Coinbase INTX API, lets the user pick an asset and network,
then retrieves (or generates) a deposit address via the Coinbase
App v2 API.
"""

import requests
from loguru import logger

from services.coinbase.api.client import CoinbaseApiClient


class CoinbaseApiDeposit:
    def __init__(self, client: CoinbaseApiClient, **_):
        self.client = client

    # ── asset selection ─────────────────────────────────────────────

    @staticmethod
    def _select_asset(assets: list[dict]) -> dict | None:
        """Display deposit-eligible assets and let the user pick one.

        Each entry carries ``asset_name`` and ``account_id`` so we
        already know the Coinbase account UUID for the chosen asset.
        """
        if not assets:
            logger.warning("No deposit-eligible assets found")
            return None

        logger.info("Available assets ({} total):", len(assets))
        cols = 4
        for i, asset in enumerate(assets, 1):
            end = "\n" if i % cols == 0 else ""
            print(f"  {i:3d}. {asset['asset_name']:<12s}", end=end)
        if len(assets) % cols != 0:
            print()

        choice = input("\n[?] Enter number or ticker: ").strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(assets):
                return assets[idx]
        else:
            upper = choice.upper()
            for asset in assets:
                if asset["asset_name"] == upper:
                    return asset
        return None

    # ── network selection ───────────────────────────────────────────

    @staticmethod
    def _select_network(networks: list[dict], ticker: str) -> str | None:
        """Display available networks and let the user pick one.

        Returns the ``network_name`` string (e.g. ``"ethereum"``,
        ``"base"``) to pass to the Coinbase address API, or ``None``
        to use the asset's default network.
        """
        if not networks:
            logger.info("No network info available for {} — using default", ticker)
            return None

        if len(networks) == 1:
            name = networks[0]["network_name"]
            display = networks[0]["display_name"]
            logger.info("Single network for {}: {} — auto-selecting", ticker, display)
            return name

        default_name = None
        logger.info("Available networks for {}:", ticker)
        for i, net in enumerate(networks, 1):
            display = net.get("display_name", net["network_name"])
            default_tag = " (default)" if net.get("is_default") else ""
            confirms = net.get("network_confirms", "?")
            min_amt = net.get("min_withdrawal_amt", "?")
            max_amt = net.get("max_withdrawal_amt", "?")
            print(
                f"  {i:3d}. {display:<22s}{default_tag:<11s}"
                f" confirms: {confirms:<5}  min: {min_amt}, max: {max_amt}"
            )
            if net.get("is_default"):
                default_name = net["network_name"]

        choice = input("[?] Enter network number (or blank for default): ").strip()
        if not choice:
            return default_name

        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(networks):
                return networks[idx]["network_name"]
        return default_name

    # ── address resolution ──────────────────────────────────────────

    def _get_or_create_address(
        self, account_id: str, ticker: str, network: str | None = None
    ) -> dict:
        """Return an existing address for the given network, or create one.

        When a network is specified, only addresses matching that network
        are considered.  Falls back to creating a new address if listing
        fails (Coinbase may return 500 on freshly-created accounts).
        """
        try:
            logger.info("Checking existing addresses for {}...", ticker)
            addresses = self.client.list_addresses(account_id)

            for addr in addresses:
                if network and addr.get("network") != network:
                    continue
                logger.info(
                    "Using existing {} address for {}", addr.get("network"), ticker
                )
                return addr
        except requests.HTTPError as e:
            logger.warning(
                "Failed to list addresses for {} ({}), will create new one",
                ticker,
                e.response.status_code,
            )

        logger.info("Generating new address for {} (network={})...", ticker, network)
        try:
            return self.client.create_address(account_id, network=network)
        except requests.HTTPError as e:
            if e.response.status_code == 400:
                detail = ""
                try:
                    errors = e.response.json().get("errors", [])
                    detail = errors[0].get("message", "") if errors else ""
                except Exception:
                    pass
                logger.error(
                    "Cannot create {} address on network '{}': {}",
                    ticker,
                    network,
                    detail or "invalid network for asset",
                )
                return {}
            raise

    # ── main entry point ────────────────────────────────────────────

    def run(self) -> None:
        # 1. Fetch user's Coinbase accounts and INTX deposit-eligible
        #    assets, then intersect them so we only show assets the
        #    user actually has a wallet for on Coinbase.
        logger.info("Fetching accounts and deposit-eligible assets...")
        accounts = self.client.list_accounts()
        account_map: dict[str, str] = {}
        for acct in accounts:
            code = acct.get("currency", {}).get("code")
            if code and acct.get("currency", {}).get("type") != "fiat":
                account_map[code] = acct["id"]

        intx_assets = CoinbaseApiClient.get_deposit_assets()
        intx_tickers = {a["asset_name"] for a in intx_assets}

        assets = [
            {"asset_name": ticker, "account_id": acct_id}
            for ticker, acct_id in sorted(account_map.items())
            if ticker in intx_tickers
        ]

        selection = self._select_asset(assets)
        if not selection:
            logger.warning("Invalid selection — aborting")
            return

        ticker = selection["asset_name"]
        account_id = selection["account_id"]

        # 2. Fetch supported networks for the chosen asset
        logger.info("Fetching networks for {}...", ticker)
        networks = CoinbaseApiClient.get_supported_networks(ticker)
        network = self._select_network(networks, ticker)

        # 3. Get or create a deposit address
        address_data = self._get_or_create_address(account_id, ticker, network)

        address = address_data.get("address", "")
        if not address:
            return

        addr_network = address_data.get("network", "")
        deposit_uri = address_data.get("deposit_uri", "")

        print("\n── Deposit info ──────────────────────────")
        print(f"  Asset:   {ticker}")
        if addr_network:
            print(f"  Network: {addr_network}")
        print(f"  Address: {address}")
        if deposit_uri:
            print(f"  URI:     {deposit_uri}")
        print("──────────────────────────────────────────")
