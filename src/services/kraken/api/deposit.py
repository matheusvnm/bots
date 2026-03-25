"""
Kraken API deposit action.

Lists available assets, lets the user pick one, discovers
deposit methods/networks, then retrieves or generates a
deposit address via the Kraken REST API.
"""

from loguru import logger

from services.kraken.api.client import KrakenApiClient, KrakenApiError


class KrakenApiDeposit:
    def __init__(self, client: KrakenApiClient, **_):
        self.client = client

    def _select_asset(self) -> str | None:
        """Prompt the user to enter an asset ticker for deposit."""
        logger.info("Fetching current balances to show known assets...")
        try:
            balances = self.client.get_balance()
            known_assets = sorted(
                asset
                for asset, amt in balances.items()
                if not asset.endswith((".S", ".M", ".B", ".F", ".T"))
            )
            if known_assets:
                logger.info("Known assets in your account:")
                for asset in known_assets:
                    bal = balances.get(asset, "0")
                    print(f"    {asset:<10s} (balance: {bal})")
        except KrakenApiError as exc:
            logger.warning("Could not fetch balances: {}", exc)

        asset = (
            input("\n[?] Asset ticker to deposit (e.g. XBT, ETH, USDT, SOL): ")
            .strip()
            .upper()
        )
        return asset if asset else None

    def _select_method(self, methods: list[dict]) -> dict | None:
        """Let the user pick a deposit method/network."""
        if not methods:
            logger.warning("No deposit methods available")
            return None

        logger.info("Available networks ({} total):", len(methods))
        for i, m in enumerate(methods, 1):
            method_name = m.get("method", "unknown")
            limit = m.get("limit", False)
            fee = m.get("fee", "0")
            min_amount = m.get("minimum", "—")
            parts = [f"fee: {fee}"]
            if min_amount and min_amount != "—":
                parts.append(f"min: {min_amount}")
            if limit:
                parts.append(f"limit: {limit}")
            detail = ", ".join(parts)
            print(f"  {i:4d}. {method_name:<40s} ({detail})")

        if len(methods) == 1:
            return methods[0]

        choice = input("[?] Select network number: ").strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(methods):
                return methods[idx]

        logger.warning("Invalid selection")
        return None

    def _select_address(
        self,
        addresses: list[dict],
        method_name: str,
        can_generate: bool,
    ) -> dict | None:
        """Let the user pick an existing address or generate a new one.

        Returns:
            An address dict to use, or ``None`` when the user asked
            to generate a new one (only offered when *can_generate*).
        """
        if not addresses:
            return None

        print(f"\n  Existing addresses for {method_name}:")
        for i, addr in enumerate(addresses, 1):
            a = addr.get("address", "")
            tag = addr.get("tag")
            label = a
            if tag:
                label = f"{a} (tag: {tag})"
            print(f"  {i:4d}. {label}")

        if can_generate:
            print(f"  {len(addresses) + 1:4d}. [Generate new address]")

        choice = input("[?] Select address number: ").strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(addresses):
                return addresses[idx]
            if can_generate and idx == len(addresses):
                return None  # caller will generate
        return addresses[0]

    def run(self) -> None:
        asset = self._select_asset()
        if not asset:
            logger.warning("No asset provided — aborting")
            return

        # Discover deposit methods for this asset
        logger.info("Fetching deposit methods for {}...", asset)
        try:
            methods = self.client.get_deposit_methods(asset)
        except KrakenApiError as exc:
            logger.error(
                "Failed to get deposit methods for {}: {}",
                asset,
                exc,
            )
            return

        method_info = self._select_method(methods)
        if not method_info:
            return

        method_name = method_info.get("method", "")
        can_generate = bool(method_info.get("gen-address"))
        logger.info("Using network: {}", method_name)

        # Fetch existing addresses
        logger.info(
            "Fetching deposit addresses for {} via {}...",
            asset,
            method_name,
        )
        try:
            addresses = self.client.get_deposit_addresses(asset, method_name)
        except KrakenApiError as exc:
            logger.error("Failed to get deposit addresses: {}", exc)
            addresses = []

        if not addresses and not can_generate:
            logger.error(
                "No deposit addresses found for {} via {} "
                "and this method does not support generating "
                "new addresses",
                asset,
                method_name,
            )
            return

        # Let user pick an existing address or generate new
        addr_info = self._select_address(addresses, method_name, can_generate)

        if addr_info is None:
            if not can_generate:
                logger.error("This method does not support generating new addresses")
                return

            logger.info("Generating new deposit address...")
            try:
                new_addrs = self.client.get_deposit_addresses(
                    asset, method_name, new=True
                )
            except KrakenApiError as exc:
                logger.error(
                    "Failed to generate deposit address: {}",
                    exc,
                )
                return

            if not new_addrs:
                logger.error(
                    "Could not generate a deposit address for {}",
                    asset,
                )
                return
            addr_info = new_addrs[0]

        address = addr_info.get("address", "")
        tag = addr_info.get("tag", "")
        expiretm = addr_info.get("expiretm", "")

        print("\n── Deposit info ──────────────────────────")
        print(f"  Asset:   {asset}")
        print(f"  Network: {method_name}")
        print(f"  Address: {address}")
        if tag:
            print(f"  Tag/Memo: {tag}")
        if expiretm and expiretm != "0":
            print(f"  Expires: {expiretm}")
        print("──────────────────────────────────────────")
