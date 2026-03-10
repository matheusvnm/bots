import time
from typing import Any

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

        logger.debug("AssetListingInterceptor captured: {}", response.url)
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
            logger.exception(f"AssetListingInterceptor failed to parse response JSON")

    def get(self, timeout: float = 10.0) -> list[dict[str, Any]]:
        deadline = time.monotonic() + timeout
        while not self._captured:
            if time.monotonic() > deadline:
                raise RuntimeError(
                    "Timeout waiting for markets/assets response — check logs/network_debug.log"
                )
            time.sleep(0.05)

        return self._captured


class AccountBalanceInterceptor:
    """Captures the account/v2/balance API response the browser makes on portfolio load."""

    def __init__(self):
        self._captured: list = []

    def __call__(self, response: Response) -> None:
        if "account/v2/balance" not in response.url:
            return

        if self._captured:
            return

        logger.debug("AccountBalanceInterceptor captured: {}", response.url)
        try:
            data = response.json().get("result", {}).get("account", {}).get("data", [])
            for item in data:
                if not item.get("asset_type") not in ("crypto", "stable_coin"):
                    continue

                if float(item.get("balance", "0")) <= 0:
                    continue

                self._captured.append(item)
        except Exception:
            logger.exception("AccountBalanceInterceptor failed to parse response JSON")

    def get(self, timeout: float = 10.0) -> list:
        deadline = time.monotonic() + timeout
        while not self._captured:
            if time.monotonic() > deadline:
                raise RuntimeError(
                    "Timeout waiting for account/v2/balance response — check logs/network_debug.log"
                )
            time.sleep(0.05)
        return self._captured


class MarketCapInterceptor:
    """Captures the markets/market-cap response to rank zero-balance assets by market cap."""

    def __init__(self):
        self._ranks: dict[str, int] = {}

    def __call__(self, response: Response) -> None:
        if "markets/market-cap" not in response.url:
            return

        if self._ranks:
            return

        logger.debug("MarketCapInterceptor captured: {}", response.url)
        try:
            result = response.json().get("result", {}).get("data", {})
            for short_name, data in result.items():
                rank = data.get("market_cap_rank", 0) 
                self._ranks[short_name] = rank if rank > 0 else 999_999

            logger.debug("MarketCapInterceptor: {} assets ranked", len(self._ranks))
        except Exception:
            logger.exception("MarketCapInterceptor failed to parse response JSON")

    def rank(self, asset: str) -> int:
        """Return position of asset in market cap order (lower = higher cap). Unknown → end."""
        return self._ranks.get(asset, 999_999)
