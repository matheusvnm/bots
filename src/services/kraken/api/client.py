"""
Kraken REST API client with HMAC-SHA512 authentication.

Handles nonce generation, request signing, and HTTP communication
with the Kraken private (authenticated) API endpoints.
"""

import base64
import hashlib
import hmac
import time
import urllib.parse
from typing import Any

import requests
from loguru import logger

from services.kraken.constants import KrakenApi


class KrakenApiError(Exception):
    """Raised when the Kraken API returns a non-empty error array."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(", ".join(errors))


class KrakenApiClient:
    """Client for interacting with the Kraken REST API."""

    def __init__(self, api_key: str, api_secret: str):
        self._api_key = api_key
        self._api_secret = api_secret
        self._session = requests.Session()

    @staticmethod
    def _generate_nonce() -> int:
        """Return an always-increasing nonce (millisecond UNIX timestamp)."""
        return int(time.time() * 1000)

    def _sign(self, url_path: str, data: dict) -> str:
        """Compute the API-Sign header value.

        Signature algorithm:
            API-Sign = Base64(
                HMAC-SHA512(
                    key     = Base64Decode(api_secret),
                    message = url_path + SHA256(nonce + POST_data)
                )
            )
        """
        post_data = urllib.parse.urlencode(data)
        encoded = (str(data["nonce"]) + post_data).encode()
        message = url_path.encode() + hashlib.sha256(encoded).digest()

        secret_bytes = base64.b64decode(self._api_secret)
        mac = hmac.new(secret_bytes, message, hashlib.sha512)
        return base64.b64encode(mac.digest()).decode()

    def _post(self, path: str, extra_data: dict | None = None) -> dict[str, Any]:
        """Execute an authenticated POST request to a private endpoint.

        Args:
            path: API path starting with ``/0/private/...``.
            extra_data: Additional POST body parameters beyond ``nonce``.

        Returns:
            The ``result`` dict from the response.

        Raises:
            KrakenApiError: If the response ``error`` array is non-empty.
        """
        url = f"{KrakenApi.BASE_URL}{path}"
        data: dict[str, Any] = {"nonce": self._generate_nonce()}
        if extra_data:
            data.update(extra_data)

        signature = self._sign(path, data)
        headers = {
            "API-Key": self._api_key,
            "API-Sign": signature,
            "Content-Type": "application/x-www-form-urlencoded",
        }

        logger.debug("POST {}", path)
        resp = self._session.post(url, headers=headers, data=data)
        resp.raise_for_status()

        body = resp.json()
        errors = body.get("error", [])
        if errors:
            raise KrakenApiError(errors)

        return body.get("result", {})

    # ── Balance ──────────────────────────────────────────────

    def get_balance(self) -> dict[str, str]:
        """POST /0/private/Balance — all cash balances.

        Returns:
            Dict keyed by asset name (e.g. ``XXBT``, ``ZUSD``) with
            string balance values.
        """
        return self._post("/0/private/Balance")

    def get_extended_balance(self) -> dict[str, dict]:
        """POST /0/private/BalanceEx — extended balances with hold info."""
        return self._post("/0/private/BalanceEx")

    # ── Deposit ──────────────────────────────────────────────

    def get_deposit_methods(self, asset: str) -> list[dict]:
        """POST /0/private/DepositMethods — available deposit methods.

        Args:
            asset: Asset code (e.g. ``XBT``, ``ETH``).
        """
        return self._post("/0/private/DepositMethods", {"asset": asset})

    def get_deposit_addresses(
        self, asset: str, method: str, new: bool = False
    ) -> list[dict]:
        """POST /0/private/DepositAddresses — get or generate deposit addresses.

        Args:
            asset: Asset code.
            method: Deposit method name (from ``get_deposit_methods``).
            new: If True, generate a new address.
        """
        data: dict[str, Any] = {"asset": asset, "method": method}
        if new:
            data["new"] = "true"
        return self._post("/0/private/DepositAddresses", data)

    def get_deposit_status(self, asset: str | None = None) -> list[dict]:
        """POST /0/private/DepositStatus — recent deposit status."""
        data = {}
        if asset:
            data["asset"] = asset
        return self._post("/0/private/DepositStatus", data or None)

    # ── Withdrawal ───────────────────────────────────────────

    def get_withdrawal_methods(self, asset: str | None = None) -> list[dict]:
        """POST /0/private/WithdrawMethods — available withdrawal methods."""
        data = {}
        if asset:
            data["asset"] = asset
        return self._post("/0/private/WithdrawMethods", data or None)

    def get_withdrawal_addresses(
        self, asset: str | None = None, method: str | None = None
    ) -> list[dict]:
        """POST /0/private/WithdrawAddresses — pre-configured withdrawal addresses."""
        data = {}
        if asset:
            data["asset"] = asset
        if method:
            data["method"] = method
        return self._post("/0/private/WithdrawAddresses", data or None)

    def get_withdrawal_info(self, asset: str, key: str, amount: str) -> dict:
        """POST /0/private/WithdrawInfo — fee estimation for a withdrawal.

        Args:
            asset: Asset code.
            key: Withdrawal address name (pre-configured in Kraken).
            amount: Amount to withdraw.
        """
        return self._post(
            "/0/private/WithdrawInfo",
            {"asset": asset, "key": key, "amount": amount},
        )

    def withdraw(self, asset: str, key: str, amount: str) -> dict:
        """POST /0/private/Withdraw — execute a withdrawal.

        Args:
            asset: Asset code.
            key: Withdrawal address name (pre-configured in Kraken).
            amount: Amount to withdraw.
        """
        return self._post(
            "/0/private/Withdraw",
            {"asset": asset, "key": key, "amount": amount},
        )

    def get_withdrawal_status(self, asset: str | None = None) -> list[dict]:
        """POST /0/private/WithdrawStatus — recent withdrawal status."""
        data = {}
        if asset:
            data["asset"] = asset
        return self._post("/0/private/WithdrawStatus", data or None)

    # ── Validation ───────────────────────────────────────────

    def validate(self) -> bool:
        """Make a lightweight API call to verify the key is valid."""
        try:
            self.get_balance()
            return True
        except Exception as exc:
            logger.warning("API key validation failed: {}", exc)
            return False
