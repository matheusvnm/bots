"""
Kraken API balance action.

Fetches all account balances via POST /0/private/Balance,
filtering out zero balances and grouping staked/earn variants
with their base asset.
"""

from loguru import logger

from services.kraken.api.client import KrakenApiClient

# Suffixes Kraken appends for staked/earn/tokenized variants
_EARN_SUFFIXES = (".S", ".M", ".B", ".F", ".T")


class KrakenApiBalance:
    def __init__(self, client: KrakenApiClient, **_):
        self.client = client

    def run(self) -> None:
        logger.info("Fetching account balances via Kraken API...")
        raw_balances = self.client.get_balance()

        # Group earn variants under their base asset
        balances: dict[str, dict[str, str]] = {}
        for asset, amount in raw_balances.items():
            if float(amount) == 0:
                continue

            # Determine the base asset and variant label
            variant = ""
            base_asset = asset
            for suffix in _EARN_SUFFIXES:
                if asset.endswith(suffix):
                    base_asset = asset[: -len(suffix)]
                    variant = suffix
                    break

            balances[asset] = {
                "base_asset": base_asset,
                "variant": variant,
                "balance": amount,
            }
            logger.debug("Asset: {} = {} (base={})", asset, amount, base_asset)

        logger.info("Balance query complete — {} asset(s) with balance", len(balances))

        print("\n── Balance info ──────────────────────────")
        if not balances:
            print("  No assets with balance found.")
        for asset, info in balances.items():
            label = asset
            if info["variant"]:
                label = f"{asset} ({info['variant'].lstrip('.')} variant)"
            print(f"  Asset:   {label}")
            print(f"  Balance: {info['balance']}")
            print("──────────────────────────────────────────")
