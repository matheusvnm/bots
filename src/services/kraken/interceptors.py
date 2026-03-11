from collections import defaultdict
from decimal import Decimal
import time
from typing import Any

from components.dtos import (
    CryptoBalance,
    CryptoNetwork,
    CryptoNetworkAddress,
    CryptoNetworkFee,
)
from loguru import logger
from patchright.sync_api import Response


class AssetListingInterceptor:
    """Captures the markets/assets API response the browser makes on portfolio load."""

    def __init__(self):
        self._captured: list = []

    def __call__(self, response: Response) -> None:
        if "/internal/markets/assets" not in response.url:
            return

        if self._captured:
            return

        logger.debug("The asset listing response captured: {}", response.url)
        try:
            result = response.json().get("result", [])
            for item in result:
                if not item.get("subclass") in ("crypto", "stable_coin"):
                    continue

                if not item.get("status") == "enabled":
                    continue

                if not item.get("enabled_for_user"):
                    continue

                self._captured.append(item)
        except Exception:
            logger.exception(f"The asset listing response failed to be parsed as JSON")
            raise

    def get(self, timeout: float = 10.0) -> list[dict[str, Any]]:
        deadline = time.monotonic() + timeout
        while not self._captured:
            if time.monotonic() > deadline:
                raise RuntimeError(
                    "Timeout waiting for internal/markets/assets response — check logs/network_debug.log"
                )
            time.sleep(0.05)

        return self._captured


class AccountBalanceInterceptor:
    """Captures the account/v2/balance API response the browser makes on portfolio load."""

    def __init__(self):
        self._assets_balance: dict[str, dict[str, Decimal]] = defaultdict(dict)

    def __call__(self, response: Response) -> None:
        if "account/v2/balance" not in response.url:
            return

        if self._assets_balance:
            return

        logger.info("The account balance response captured: {}", response.url)
        try:
            data = response.json().get("result", {}).get("account", {}).get("data", [])
            for item in data:
                if not item.get("asset_type") not in ("crypto", "stable_coin"):
                    continue

                value = Decimal(item["balance"])
                usd_value = Decimal(item["quote_balance"])
                if usd_value <= Decimal("0.0"):
                    continue

                crypto_balance = CryptoBalance(value=value, usd_value=usd_value)

                self._assets_balance[item["asset"]] = crypto_balance
        except Exception:
            logger.exception("The account balance response failed to be parsed as JSON")
            raise

    def get(self, asset: str, timeout: float = 10.0) -> CryptoBalance:
        deadline = time.monotonic() + timeout
        while not self._assets_balance:
            if time.monotonic() > deadline:
                raise RuntimeError(
                    "Timeout waiting for account/v2/balance response — check logs/network_debug.log"
                )
            time.sleep(0.05)

        return self._assets_balance.get(asset)


class MarketCapInterceptor:
    """Captures the markets/market-cap response to rank zero-balance assets by market cap."""

    def __init__(self):
        self._ranks: dict[str, int] = {}

    def __call__(self, response: Response) -> None:
        if "markets/market-cap" not in response.url:
            return

        if self._ranks:
            return

        logger.info("The market cap were captured: {}", response.url)
        try:
            result = response.json().get("result", {}).get("data", {})
            for short_name, data in result.items():
                self._ranks[short_name] = data.get("market_cap_rank", 999_999)

            logger.info("We processed {} assets market cap ranks", len(self._ranks))
        except Exception:
            logger.exception("The market cap response failed to be parsed as JSON")
            raise

    def get(self, asset: str, timeout: float = 10.0) -> int:
        """Return position of asset in market cap order (lower = higher cap). Unknown → end."""
        deadline = time.monotonic() + timeout
        while not self._ranks:
            if time.monotonic() > deadline:
                raise RuntimeError(
                    "Timeout waiting for markets/market-cap response — check logs/network_debug.log"
                )
            time.sleep(0.05)

        return self._ranks.get(asset, 999_999)


class NetworkInterceptor:
    """Captures the methods for deposit/withdraw response that has information about networks/addresses."""

    def __init__(self):
        self._networks: dict[str, list[CryptoNetwork]] = defaultdict(list)

    def __call__(self, response: Response) -> None:
        if "deposits/methods" not in response.url:
            return

        logger.info("The methods were captured: {}", response.url)
        try:
            methods = response.json().get("result", [])
            for method in methods:
                if method.get("type") == "bank":
                    continue

                if "deposit_network_info" not in method or "asset" not in method:
                    continue

                asset = method["asset"]
                sort_weight = (method.get("sort_weight", 999_999),)

                fee = None
                if method.get("fee") or method.get("fee_percentage"):
                    fee = CryptoNetworkFee(
                        fee=Decimal(method.get("fee", "0.0")),
                        fee_percentage=Decimal(method.get("fee_percentage", "0.0")),
                    )

                addresses = []
                address_info: dict[str, str]
                for address_info in method.get("information", []):
                    addresses.append(
                        CryptoNetworkAddress(
                            address=address_info.get("address"),
                            tag=address_info.get("tag"),
                        )
                    )

                limits = method.get("limits", {})
                minimum_amount = limits.get("minimum", None)
                maximum_amount = limits.get("maximum", None)

                network_info = method["deposit_network_info"]
                network = CryptoNetwork(
                    name=network_info.get("network"),
                    confirmations=network_info.get("confirmations", 0),
                    confirmation_time=network_info.get("confirmation_time", ""),
                    minimum_amount=minimum_amount,
                    maximum_amount=maximum_amount,
                    fee=fee,
                    sort_weight=sort_weight,
                    addresses=addresses,
                )

                self._networks[asset].append(network)

            logger.info("We processed {} assets networks", len(self._networks))
        except Exception:
            logger.exception("The methods response failed to be parsed as JSON")
            raise

    def get(self, asset: str, timeout: float = 10.0) -> list[CryptoNetwork]:
        """Return a crypto network."""
        deadline = time.monotonic() + timeout
        while not self._networks:
            if time.monotonic() > deadline:
                raise RuntimeError(
                    "Timeout waiting for deposits/methods response — check logs/network_debug.log"
                )
            time.sleep(0.05)

        networks = self._networks.get(asset, [])
        networks.sort()
        return networks


class NetworkAddressesInterceptor:
    """Captures the for deposit/withdraw response that has information about addresses."""

    def __init__(self):
        self._addresses: dict[str, list[CryptoNetworkAddress]] = defaultdict(list)

    def __call__(self, response: Response) -> None:
        if "deposits/addresses" not in response.url:
            return

        logger.info("The methods were captured: {}", response.url)
        try:
            addresses_info = response.json().get("result", [])
            for address_info in addresses_info:

                if "asset" not in address_info:
                    continue
                
                asset = address_info["asset"]
                crypto_address = CryptoNetworkAddress(address=address_info["address"], tag=address_info["tag"])
                self._addresses[asset].append(crypto_address)

            logger.info("We processed {} network addresses", len(self._addresses))
        except Exception:
            logger.exception("The methods response failed to be parsed as JSON")
            raise

    def get(self, asset: str, timeout: float = 10.0) -> list[CryptoNetwork]:
        """Return a crypto network addresses."""
        deadline = time.monotonic() + timeout
        while not self._addresses:
            if time.monotonic() > deadline:
                raise RuntimeError(
                    "Timeout waiting for deposits/addresses response — check logs/network_debug.log"
                )
            time.sleep(0.05)

        addresses = self._addresses.get(asset, [])
        addresses.sort()
        return addresses
