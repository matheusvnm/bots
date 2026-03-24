"""
Coinbase API withdraw (send) action.

Lists accounts with non-zero balances, lets the user pick one,
then sends crypto to a destination address via
POST /v2/accounts/:id/transactions.

Includes travel rule data collection required by certain
jurisdictions (e.g. BR, EU, GB).
"""

import uuid

import requests
from loguru import logger

from services.coinbase.api.client import CoinbaseApiClient


class CoinbaseApiWithdraw:
    def __init__(self, client: CoinbaseApiClient, **_):
        self.client = client

    def _select_account(self, accounts: list[dict]) -> dict | None:
        """Display crypto accounts with balance and let the user pick one."""
        funded = [
            a
            for a in accounts
            if a.get("currency", {}).get("type") != "fiat"
            and float(a.get("balance", {}).get("amount", "0")) > 0
        ]
        if not funded:
            logger.warning("No funded crypto accounts found")
            return None

        logger.info("Funded assets ({} total):", len(funded))
        for i, acct in enumerate(funded, 1):
            code = acct["currency"]["code"]
            name = acct["currency"]["name"]
            bal = acct["balance"]["amount"]
            print(f"  {i:4d}. {code:<8s} {name:<24s} (balance: {bal})")

        choice = input("[?] Enter number or ticker: ").strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(funded):
                return funded[idx]
        else:
            upper = choice.upper()
            for acct in funded:
                if acct["currency"]["code"] == upper:
                    return acct
        return None

    def _collect_travel_rule(self) -> dict:
        """Prompt for travel rule data required by Coinbase."""
        print("\n  Travel rule information required:")

        is_self = input("[?] Is this your own wallet? (y/n): ").strip().lower()
        is_self_val = "IS_SELF_TRUE" if is_self in ("y", "yes") else "IS_SELF_FALSE"

        wallet_type = input("[?] Wallet type — (1) self-hosted, (2) exchange: ").strip()
        wallet_type_val = (
            "WALLET_TYPE_EXCHANGE" if wallet_type == "2" else "WALLET_TYPE_SELF_HOSTED"
        )

        name = input("[?] Beneficiary name: ").strip()
        country = (
            input("[?] Beneficiary country (ISO 2-letter, e.g. BR): ").strip().upper()
        )

        data: dict = {
            "is_self": is_self_val,
            "beneficiary_wallet_type": wallet_type_val,
            "beneficiary_name": name,
            "beneficiary_address": {"country": country},
        }

        if wallet_type_val == "WALLET_TYPE_EXCHANGE":
            institution = input(
                "[?] Exchange VASP ID (see docs.cdp.coinbase.com/coinbase-app/"
                "transfer-apis/vasps): "
            ).strip()
            if institution:
                data["beneficiary_financial_institution"] = institution

        return data

    def run(self) -> None:
        logger.info("Fetching accounts via API...")
        accounts = self.client.list_accounts()

        account = self._select_account(accounts)
        if not account:
            logger.warning("Invalid selection — aborting")
            return

        ticker = account["currency"]["code"]
        account_id = account["id"]
        available = account["balance"]["amount"]

        print(f"\n  Asset:     {ticker}")
        print(f"  Available: {available}")

        to = input("[?] Destination address: ").strip()
        if not to:
            logger.warning("No address provided — aborting")
            return

        amount = input(f"[?] Amount to send (max {available}): ").strip()
        if not amount:
            logger.warning("No amount provided — aborting")
            return

        network = input("[?] Network (leave blank for default): ").strip() or None

        travel_rule = self._collect_travel_rule()

        idem = str(uuid.uuid4())
        logger.info("Sending {} {} to {} (idem: {})", amount, ticker, to, idem)

        try:
            tx = self.client.send_money(
                account_id=account_id,
                to=to,
                amount=amount,
                currency=ticker,
                network=network,
                idem=idem,
                travel_rule_data=travel_rule,
            )
        except requests.HTTPError as e:
            detail = ""
            try:
                errors = e.response.json().get("errors", [])
                detail = errors[0].get("message", "") if errors else ""
            except Exception:
                pass
            logger.error("Withdraw failed: {}", detail or e)
            return

        status = tx.get("status", "unknown")
        tx_id = tx.get("id", "")

        print("\n── Withdraw info ─────────────────────────")
        print(f"  Asset:   {ticker}")
        print(f"  Amount:  {amount}")
        print(f"  To:      {to}")
        if network:
            print(f"  Network: {network}")
        print(f"  Status:  {status}")
        print(f"  TX ID:   {tx_id}")
        print("──────────────────────────────────────────")
