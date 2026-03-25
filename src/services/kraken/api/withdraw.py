"""
Kraken API withdraw action.

Lists funded assets, lets the user pick one, shows pre-configured
withdrawal addresses, estimates fees, and executes the withdrawal
via the Kraken REST API.

Note: Kraken requires withdrawal addresses to be pre-configured
in the web UI. The API references them by a ``key`` name string.
"""

from loguru import logger

from services.kraken.api.client import KrakenApiClient, KrakenApiError


class KrakenApiWithdraw:
    def __init__(self, client: KrakenApiClient, **_):
        self.client = client

    def _select_funded_asset(self, balances: dict[str, str]) -> str | None:
        """Display funded assets and let the user pick one."""
        funded = {
            asset: bal
            for asset, bal in balances.items()
            if float(bal) > 0 and not asset.endswith((".S", ".M", ".B", ".F", ".T"))
        }
        if not funded:
            logger.warning("No funded assets found")
            return None

        logger.info("Funded assets ({} total):", len(funded))
        assets_list = sorted(funded.keys())
        for i, asset in enumerate(assets_list, 1):
            print(f"  {i:4d}. {asset:<10s} (balance: {funded[asset]})")

        choice = input("[?] Enter number or ticker: ").strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(assets_list):
                return assets_list[idx]
        else:
            upper = choice.upper()
            if upper in funded:
                return upper

        logger.warning("Invalid selection")
        return None

    def _select_address(self, addresses: list[dict]) -> dict | None:
        """Display pre-configured withdrawal addresses and let the user pick."""
        if not addresses:
            logger.warning(
                "No withdrawal addresses configured for this asset. "
                "Please add withdrawal addresses in the Kraken web UI first."
            )
            return None

        logger.info("Pre-configured withdrawal addresses ({} total):", len(addresses))
        for i, addr in enumerate(addresses, 1):
            key_name = addr.get("key", "unknown")
            address = addr.get("address", "")
            method = addr.get("method", "")
            verified = addr.get("verified", False)
            print(
                f"  {i:4d}. {key_name:<20s} "
                f"({method}, addr: {address[:30]}..., verified: {verified})"
            )

        choice = input("[?] Select address number: ").strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(addresses):
                return addresses[idx]

        logger.warning("Invalid selection")
        return None

    def run(self) -> None:
        logger.info("Fetching account balances...")
        try:
            balances = self.client.get_balance()
        except KrakenApiError as exc:
            logger.error("Failed to fetch balances: {}", exc)
            return

        asset = self._select_funded_asset(balances)
        if not asset:
            return

        available = balances.get(asset, "0")
        print(f"\n  Asset:     {asset}")
        print(f"  Available: {available}")

        # List pre-configured withdrawal addresses
        logger.info("Fetching withdrawal addresses for {}...", asset)
        try:
            addresses = self.client.get_withdrawal_addresses(asset=asset)
        except KrakenApiError as exc:
            logger.error("Failed to fetch withdrawal addresses: {}", exc)
            return

        addr_info = self._select_address(addresses)
        if not addr_info:
            return

        key_name = addr_info.get("key", "")
        amount = input(f"[?] Amount to withdraw (max {available}): ").strip()
        if not amount:
            logger.warning("No amount provided — aborting")
            return

        # Show fee estimate before executing
        logger.info("Estimating withdrawal fee...")
        try:
            fee_info = self.client.get_withdrawal_info(asset, key_name, amount)
            fee = fee_info.get("fee", "unknown")
            limit = fee_info.get("limit", "unknown")
            logger.info("Fee: {}, Limit: {}", fee, limit)

            confirm = (
                input(f"[?] Withdraw {amount} {asset} (fee: {fee})? (y/n): ")
                .strip()
                .lower()
            )
            if confirm not in ("y", "yes"):
                logger.info("Withdrawal cancelled")
                return
        except KrakenApiError as exc:
            logger.warning("Could not estimate fee: {}", exc)
            confirm = (
                input(
                    f"[?] Proceed with withdrawal of {amount} {asset} "
                    f"without fee estimate? (y/n): "
                )
                .strip()
                .lower()
            )
            if confirm not in ("y", "yes"):
                logger.info("Withdrawal cancelled")
                return

        # Execute the withdrawal
        logger.info("Executing withdrawal of {} {} to {}...", amount, asset, key_name)
        try:
            result = self.client.withdraw(asset, key_name, amount)
        except KrakenApiError as exc:
            logger.error("Withdrawal failed: {}", exc)
            return

        refid = result.get("refid", "unknown")

        print("\n── Withdraw info ─────────────────────────")
        print(f"  Asset:       {asset}")
        print(f"  Amount:      {amount}")
        print(f"  Destination: {key_name}")
        print(f"  Ref ID:      {refid}")
        print("──────────────────────────────────────────")
