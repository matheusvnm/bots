from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from components.dtos import Credentials, Credentials
from loguru import logger
from components.trace import PageTracer
from services.coinbase.balance import CoinbaseBalance
from services.coinbase.deposit import CoinbaseDeposit
from services.coinbase.login import CoinbaseAuthenticator
from services.coinbase.withdraw import CoinbaseWithdraw
from services.kraken.deposit import KrakenDeposit
from services.kraken.login import KrakenAuthenticator
from services.kraken.withdraw import KrakenWithdraw


type KrakenAction = KrakenDeposit | KrakenWithdraw
type CoinbaseAction = CoinbaseBalance | CoinbaseDeposit | CoinbaseWithdraw


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
