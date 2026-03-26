from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from components.dtos import Credentials, Credentials
from loguru import logger
from components.trace import PageTracer
from services.coinbase.browser.balance import CoinbaseBalance
from services.coinbase.browser.deposit import CoinbaseDeposit
from services.coinbase.login import CoinbaseAuthenticator
from services.coinbase.browser.withdraw import CoinbaseWithdraw
from services.coinbase.api.balance import CoinbaseApiBalance
from services.coinbase.api.client import CoinbaseApiClient
from services.coinbase.api.deposit import CoinbaseApiDeposit
from services.coinbase.api.withdraw import CoinbaseApiWithdraw
from services.kraken.browser.deposit import KrakenDeposit
from services.kraken.login import KrakenAuthenticator
from services.kraken.browser.withdraw import KrakenWithdraw
from services.kraken.api.balance import KrakenApiBalance
from services.kraken.api.client import KrakenApiClient
from services.kraken.api.deposit import KrakenApiDeposit
from services.kraken.api.withdraw import KrakenApiWithdraw


type KrakenAction = KrakenDeposit | KrakenWithdraw
type KrakenApiAction = KrakenApiBalance | KrakenApiDeposit | KrakenApiWithdraw
type CoinbaseAction = CoinbaseBalance | CoinbaseDeposit | CoinbaseWithdraw
type CoinbaseApiAction = CoinbaseApiBalance | CoinbaseApiDeposit | CoinbaseApiWithdraw


class AbstractBot(ABC):
    @abstractmethod
    def run(self, action: str, credentials: Credentials):
        pass


class KrakenBot(AbstractBot):
    ACTIONS: dict[str, KrakenAction] = {
        "deposit": KrakenDeposit,
        "withdraw": KrakenWithdraw,
    }

    def __init__(self, tracer: PageTracer, **_: Any):
        self.tracer = tracer
        self.authenticator = KrakenAuthenticator(tracer)

    def run(self, action: str, credentials: Credentials) -> None:
        if action not in self.ACTIONS:
            available = ", ".join(self.ACTIONS)
            raise ValueError(f"Unknown action: {action!r}. Available: {available}")

        with self.authenticator.login(credentials) as (
            page,
            interceptor,
        ):
            logger.info("Starting action: {}", action)
            handler_cls = self.ACTIONS[action]
            handler = handler_cls(
                page=page, tracer=self.tracer, interceptor=interceptor
            )
            handler.run()


class CoinbaseBot(AbstractBot):
    ACTIONS: dict[str, CoinbaseAction] = {
        "balance": CoinbaseBalance,
        "deposit": CoinbaseDeposit,
        "withdraw": CoinbaseWithdraw,
    }

    def __init__(self, tracer: PageTracer, **_: Any):
        self.tracer = tracer
        self.authenticator = CoinbaseAuthenticator(tracer)

    def run(self, action: str, credentials: Credentials) -> None:
        if action not in self.ACTIONS:
            available = ", ".join(self.ACTIONS)
            raise ValueError(f"Unknown action: {action!r}. Available: {available}")

        with self.authenticator.login(credentials) as page:
            logger.info("Starting action: {}", action)
            handler_cls = self.ACTIONS[action]
            handler = handler_cls(page=page, tracer=self.tracer)
            handler.run()


class CoinbaseApiBot(AbstractBot):
    """API-based Coinbase bot.

    Credentials are loaded from disk (or provisioned via browser)
    on the first call to ``run()``.
    """

    ACTIONS: dict[str, CoinbaseApiAction] = {
        "balance": CoinbaseApiBalance,
        "deposit": CoinbaseApiDeposit,
        "withdraw": CoinbaseApiWithdraw,
    }

    def __init__(self, tracer: PageTracer, **_: Any):
        self.tracer = tracer

    def run(self, action: str, credentials: Credentials) -> None:
        logger.info("Starting API workflow for Coinbase.")
        if action not in self.ACTIONS:
            available = ", ".join(self.ACTIONS)
            raise ValueError(f"Unknown action: {action!r}. Available: {available}")

        # Resolve API credentials (load from disk or provision via browser)
        from services.coinbase.api.credentials import CoinbaseApiCredentialStore

        cred_dir = credentials.state_file_path.parent
        store = CoinbaseApiCredentialStore(base_dir=cred_dir)
        api_key, api_secret = store.get_or_provision(self.tracer, credentials)

        client = CoinbaseApiClient(api_key=api_key, api_secret=api_secret)

        logger.info("Starting action: {}", action)
        handler_cls = self.ACTIONS[action]
        handler = handler_cls(client=client)
        handler.run()


class KrakenApiBot(AbstractBot):
    """API-based Kraken bot.

    Uses browser automation only for one-time API key provisioning
    on Kraken Pro. All subsequent operations (balance, deposit,
    withdraw) use the Kraken REST API with HMAC-SHA512 authentication.
    """

    ACTIONS: dict[str, KrakenApiAction] = {
        "balance": KrakenApiBalance,
        "deposit": KrakenApiDeposit,
        "withdraw": KrakenApiWithdraw,
    }

    def __init__(self, tracer: PageTracer, **_: Any):
        self.tracer = tracer

    def run(self, action: str, credentials: Credentials) -> None:
        if action not in self.ACTIONS:
            available = ", ".join(self.ACTIONS)
            raise ValueError(f"Unknown action: {action!r}. Available: {available}")

        from services.kraken.api.credentials import KrakenApiCredentialStore

        cred_dir = credentials.state_file_path.parent
        store = KrakenApiCredentialStore(base_dir=cred_dir)
        api_key, api_secret = store.get_or_provision(self.tracer, credentials)

        client = KrakenApiClient(api_key=api_key, api_secret=api_secret)

        logger.info("Starting action: {}", action)
        handler_cls = self.ACTIONS[action]
        handler = handler_cls(client=client)
        handler.run()
