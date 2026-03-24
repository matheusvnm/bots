"""
Coinbase API deposit action.

Lists existing crypto accounts, lets the user pick one (by number or
ticker), optionally pick a network, then retrieves an existing deposit
address for that network or generates a new one.
"""

import requests
from loguru import logger

from services.coinbase.api.client import CoinbaseApiClient


class CoinbaseApiDeposit:
    def __init__(self, client: CoinbaseApiClient, **_):
        self.client = client

    def _select_account(self, accounts: list[dict]) -> dict | None:
        """Display crypto accounts and let the user pick one."""
        crypto = [a for a in accounts if a.get("currency", {}).get("type") != "fiat"]
        if not crypto:
            logger.warning("No crypto accounts found")
            return None

        logger.info("Available assets ({} total):", len(crypto))
        for i, acct in enumerate(crypto, 1):
            code = acct["currency"]["code"]
            name = acct["currency"]["name"]
            bal = acct["balance"]["amount"]
            print(f"  {i:4d}. {code:<8s} {name:<24s} (balance: {bal})")

        choice = input("[?] Enter number or ticker: ").strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(crypto):
                return crypto[idx]
        else:
            upper = choice.upper()
            for acct in crypto:
                if acct["currency"]["code"] == upper:
                    return acct
        return None

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

    def run(self) -> None:
        logger.info("Fetching accounts via API...")
        accounts = self.client.list_accounts()

        account = self._select_account(accounts)
        if not account:
            logger.warning("Invalid selection — aborting")
            return

        ticker = account["currency"]["code"]
        account_id = account["id"]

        network = (
            input(
                "[?] Network (e.g. ethereum, solana, base, polygon — "
                "leave blank for default): "
            )
            .strip()
            .lower()
            or None
        )

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
