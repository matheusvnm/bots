"""
Coinbase API balance action.

Lists all accounts with non-zero balances via GET /v2/accounts,
filtering out fiat currencies.
"""

from loguru import logger

from services.coinbase_api.client import CoinbaseApiClient


class CoinbaseApiBalance:
    def __init__(self, client: CoinbaseApiClient, **_):
        self.client = client

    def run(self) -> None:
        logger.info("Fetching account balances via API...")
        accounts = self.client.list_accounts()

        balances: dict = {}
        for account in accounts:
            currency = account.get("currency", {})

            # Skip fiat accounts
            if currency.get("type") == "fiat":
                continue

            balance = account.get("balance", {})
            amount = balance.get("amount", "0")
            code = currency.get("code", "")

            # Skip zero balances
            if float(amount) == 0:
                continue

            balances[code] = {
                "currency": balance.get("currency", code),
                "value": amount,
                "currency_value": amount,
            }
            logger.debug("Asset: {} = {}", code, amount)

        logger.info("Balance query complete — {} asset(s) found", len(balances))

        print("\n── Balance info ──────────────────────────")
        for ticker, info in balances.items():
            print(f"  Asset:          {ticker}")
            print(f"  Currency:       {info['currency']}")
            print(f"  Value:          {info['value']}")
            print(f"  Currency value: {info['currency_value']}")
            print("──────────────────────────────────────────")
