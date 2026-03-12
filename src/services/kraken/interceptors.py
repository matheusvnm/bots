from collections import defaultdict
from decimal import Decimal
import threading
import time
from typing import Any

from components.utils import get_query_params
from components.dtos import (
    CryptoAsset,
    CryptoBalance,
    CryptoNetwork,
    CryptoNetworkAddress,
    CryptoNetworkFee,
)
from loguru import logger
from patchright.sync_api import Response


class _AssetListingInterceptor:
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

    def get(self) -> list[dict[str, Any]]:
        return self._captured


class _AccountBalanceInterceptor:
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

    def get(self, asset: str) -> CryptoBalance:
        return self._assets_balance.get(asset)


class _MarketCapInterceptor:
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

    def get(self, asset: str) -> int:
        """Return position of asset in market cap order (lower = higher cap). Unknown → end."""
        return self._ranks.get(asset, 999_999)


class _NetworkInterceptor:
    """Captures the methods for deposit/withdraw response that has information about networks/addresses."""

    def __init__(self):
        self._networks: dict[str, list[CryptoNetwork]] = defaultdict(list)

    def __call__(self, response: Response) -> None:
        if "deposits/methods" not in response.url:
            return

        query_params = get_query_params(response.url)
        asset = query_params.get("asset")
        if not asset:
            return

        logger.info(f"The methods were captured: {response.url}")
        try:
            existing_networks = set(existing_network.name for existing_network in self._networks[asset])
            methods = response.json().get("result", [])
            for method in methods:
                network_info = method["deposit_network_info"]
                name = network_info.get("network")
                if name in existing_networks:
                    continue  

                fee = None
                if method.get("fee") or method.get("fee_percentage"):
                    fee = CryptoNetworkFee(
                        fee=Decimal(method.get("fee", "0.0")),
                        fee_percentage=Decimal(method.get("fee_percentage", "0.0")),
                    )

                limits = method.get("limits", {})
                minimum_amount = limits.get("minimum", None)
                maximum_amount = limits.get("maximum", None)
                sort_weight = method.get("sort_weight", 999_999)

                network = CryptoNetwork(
                    name=name,
                    confirmations=network_info.get("confirmations", 0),
                    confirmation_time=network_info.get("confirmation_time", ""),
                    minimum_amount=minimum_amount,
                    maximum_amount=maximum_amount,
                    fee=fee,
                    sort_weight=sort_weight,
                )

                self._networks[asset].append(network)

            logger.info(f"We processed {len(self._networks[asset])} assets networks")
        except Exception:
            logger.exception("The methods response failed to be parsed as JSON")
            raise

    def get(self, asset: str) -> list[CryptoNetwork]:
        """Return a crypto network."""
        networks = self._networks.get(asset, [])
        networks.sort()
        return networks


class _NetworkAddressesInterceptor:
    """Captures the for deposit/withdraw response that has information about addresses."""

    def __init__(self):
        self._addresses: dict[str, dict[str, list[CryptoNetworkAddress]]] = defaultdict(lambda: defaultdict(list))

    def __call__(self, response: Response) -> None:
        if "deposits/addresses" not in response.url:
            return

        query_params = get_query_params(response.url)

        asset = query_params.get("asset")
        network = query_params.get("method")
        if not (asset and network):
            return

        logger.info(f"The methods were captured: {response.url}")
        try:
            existing_addresses = set((address.address, address.tag,) for address in self._addresses[asset][network])


            addresses_info = response.json().get("result", [])
            for address_info in addresses_info:
                address = address_info.get("address")
                tag = address_info.get("tag")

                if (address, tag,) in existing_addresses:
                    continue

                crypto_address = CryptoNetworkAddress(address=address_info.get("address"), tag=address_info.get("tag"))
                self._addresses[asset][network].append(crypto_address)

            logger.info(f"We processed {len(self._addresses)} network addresses")
        except Exception:
            logger.exception("The methods response failed to be parsed as JSON")
            raise

    def get(self, asset: str, network: str) -> list[CryptoNetworkAddress]:
        """Return a crypto network addresses."""
        asset_addresses = self._addresses.get(asset, {})
        if len(asset_addresses) == 1:
            for v in asset_addresses.values():
                return list(v)

        addresses = asset_addresses.get(network, [])
        addresses.sort()
        return addresses


class KrakenInterceptor:
    """Facade that wraps all 5 Kraken API interceptors."""

    def __init__(self):
        self._asset_listing = _AssetListingInterceptor()
        self._account_balance = _AccountBalanceInterceptor()
        self._market_cap = _MarketCapInterceptor()
        self._network = _NetworkInterceptor()
        self._network_addresses = _NetworkAddressesInterceptor()

    def __call__(self, response: Response) -> None:
        self._asset_listing(response)
        self._account_balance(response)
        self._market_cap(response)
        self._network(response)
        self._network_addresses(response)

    def deposit_assets(self) -> list[CryptoAsset]:
        """All enabled assets, sorted by USD value desc then market cap asc."""
        raw_assets = self._asset_listing.get()

        crypto_assets = []
        for item in raw_assets:
            name = item["name"]
            asset = item["asset"]
            
            crypto_asset = CryptoAsset(
                name=name,
                asset=asset,
                balance=self._account_balance.get(asset),
                market_cap_rank=self._market_cap.get(asset),
            )
            crypto_assets.append(crypto_asset)

        crypto_assets.sort()
        return crypto_assets

    def withdraw_assets(self) -> list[CryptoAsset]:
        """Only non-zero balance assets, sorted by USD value desc."""
        withdraw_assets = []
        crypto_assets = self.deposit_assets()
        for crypto_asset in crypto_assets:
            if crypto_asset.balance and crypto_asset.balance > Decimal("0.0"):
                withdraw_assets.append(crypto_asset)

        return withdraw_assets

    def networks(self, asset: str) -> list[CryptoNetwork]:
        """Delegates to the network interceptor."""
        return self._network.get(asset)

    def addresses(self, asset: str, network: str) -> list[CryptoNetworkAddress]:
        """Delegates to the network addresses interceptor."""
        return self._network_addresses.get(asset, network)
