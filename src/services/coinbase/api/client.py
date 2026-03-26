"""
Coinbase REST API client with JWT (ES256) authentication.

Handles token generation, request signing, and HTTP communication
with both the v2 (account/wallet) and v3 (Advanced Trade) endpoints.
"""

import secrets
import time
from typing import Any

import jwt
import requests
from cryptography.hazmat.primitives import serialization
from loguru import logger

API_BASE = "https://api.coinbase.com"
INTX_API_BASE = "https://api.international.coinbase.com"


class CoinbaseApiClient:
    """Client for interacting with Coinbase REST API."""

    def __init__(self, api_key: str, api_secret: str):
        self._api_key = api_key
        self._api_secret = api_secret
        self._session = requests.Session()

    def _build_jwt(self, method: str, path: str) -> str:
        """Build a short-lived ES256 JWT for a single request."""
        private_key = serialization.load_pem_private_key(
            self._api_secret.encode(), password=None
        )
        uri = f"{method.upper()} api.coinbase.com{path}"
        payload = {
            "sub": self._api_key,
            "iss": "cdp",
            "nbf": int(time.time()),
            "exp": int(time.time()) + 120,
            "uri": uri,
        }
        headers = {
            "kid": self._api_key,
            "nonce": secrets.token_hex(),
        }
        return jwt.encode(payload, private_key, algorithm="ES256", headers=headers)

    def _headers(self, method: str, path: str) -> dict[str, str]:
        token = self._build_jwt(method, path)
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        url = f"{API_BASE}{path}"
        headers = self._headers("GET", path)
        logger.debug("GET {}", path)
        resp = self._session.get(url, headers=headers, params=params)
        resp.raise_for_status()
        return resp.json()

    def post(self, path: str, body: dict | None = None) -> dict[str, Any]:
        url = f"{API_BASE}{path}"
        headers = self._headers("POST", path)
        logger.debug("POST {}", path)
        resp = self._session.post(url, headers=headers, json=body or {})
        resp.raise_for_status()
        return resp.json()

    def list_accounts(self) -> list[dict]:
        """GET /v2/accounts — all user accounts with balances."""
        data = self.get("/v2/accounts")
        accounts = data.get("data", [])
        pagination = data.get("pagination", {})
        while pagination.get("next_uri"):
            data = self.get(pagination["next_uri"])
            accounts.extend(data.get("data", []))
            pagination = data.get("pagination", {})
        return accounts

    def get_account(self, account_id: str) -> dict:
        """GET /v2/accounts/:id — single account."""
        return self.get(f"/v2/accounts/{account_id}").get("data", {})

    def create_address(self, account_id: str, network: str | None = None) -> dict:
        """POST /v2/accounts/:id/addresses — generate a deposit address.

        Args:
            account_id: The account UUID or currency code.
            network: Optional blockchain network (e.g. "ethereum", "solana",
                     "bitcoin", "base", "polygon").  Defaults to the asset's
                     primary network when omitted.
        """
        body: dict[str, str] = {}
        if network:
            body["network"] = network
        return self.post(f"/v2/accounts/{account_id}/addresses", body=body or None).get(
            "data", {}
        )

    def list_addresses(self, account_id: str) -> list[dict]:
        """GET /v2/accounts/:id/addresses — list existing addresses."""
        return self.get(f"/v2/accounts/{account_id}/addresses").get("data", [])

    @staticmethod
    def get_deposit_assets() -> list[dict]:
        """Fetch crypto assets that support on-chain deposits.

        Uses the public (unauthenticated) Coinbase INTX API to list
        assets with ``supported_networks_enabled == True``.

        Returns:
            Sorted list of dicts with ``asset_name`` and ``asset_uuid``.
        """
        try:
            resp = requests.get(f"{INTX_API_BASE}/api/v1/assets", timeout=15)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Failed to fetch INTX assets: {}", exc)
            return []

        assets = [
            {"asset_name": a["asset_name"], "asset_uuid": a["asset_uuid"]}
            for a in resp.json()
            if a.get("supported_networks_enabled") and a.get("status") == "ACTIVE"
        ]
        assets.sort(key=lambda a: a["asset_name"])
        return assets

    @staticmethod
    def get_supported_networks(asset: str) -> list[dict]:
        """Fetch supported blockchain networks for a given asset.

        Uses the public (unauthenticated) Coinbase INTX API.

        Args:
            asset: Ticker symbol, e.g. ``"BTC"``, ``"USDC"``.

        Returns:
            List of network dicts, each containing ``network_name``,
            ``display_name``, ``is_default``, ``min_withdrawal_amt``,
            ``max_withdrawal_amt``, ``network_confirms``, and
            ``processing_time``.  Empty list when the asset is not
            found on INTX.
        """
        try:
            resp = requests.get(
                f"{INTX_API_BASE}/api/v1/assets/{asset}/networks",
                timeout=15,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning(
                "Failed to fetch networks for {}: {}",
                asset,
                exc,
            )
            return []

        networks = resp.json()
        # Put the default network first, keep the rest alphabetical.
        networks.sort(
            key=lambda n: (not n.get("is_default", False), n.get("display_name", "")),
        )
        return networks

    def send_money(
        self,
        account_id: str,
        to: str,
        amount: str,
        currency: str,
        network: str | None = None,
        destination_tag: str | None = None,
        idem: str | None = None,
        travel_rule_data: dict | None = None,
    ) -> dict:
        """POST /v2/accounts/:id/transactions — send crypto."""
        body: dict[str, Any] = {
            "type": "send",
            "to": to,
            "amount": amount,
            "currency": currency,
        }
        if network:
            body["network"] = network
        if destination_tag:
            body["destination_tag"] = destination_tag
        if idem:
            body["idem"] = idem
        if travel_rule_data:
            body["travel_rule_data"] = travel_rule_data
        return self.post(f"/v2/accounts/{account_id}/transactions", body=body).get(
            "data", {}
        )
