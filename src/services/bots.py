


from pathlib import Path
from typing import Any

from components.dtos import KrakenCredentials
from loguru import logger
from components.trace import PageTracer
from services.kraken.deposit import KrakenDeposit
from services.kraken.login import KrakenAuthenticator
from services.kraken.withdraw import KrakenWithdraw


class KrakenBot:

    ACTIONS: dict[str, type] = {
        "deposit": KrakenDeposit,
        "withdraw": KrakenWithdraw,
    }

    def __init__(self, tracer: PageTracer, **_: Any):
        self.tracer = tracer
        self.authenticator = KrakenAuthenticator(tracer)

    def run(self, credentials: KrakenCredentials, action: str, refresh: bool = False) -> None:
        if action not in self.ACTIONS:
            available = ", ".join(self.ACTIONS)
            raise ValueError(f"Unknown action: {action!r}. Available: {available}")

        state_path = Path(credentials.device_cookie_path)
        with self.authenticator.login(state_path, credentials=credentials) as page:
            logger.info("Starting action: {}", action)
            handler_cls: KrakenDeposit | KrakenWithdraw = self.ACTIONS[action]
            handler = handler_cls(tracer=self.tracer, cache_path=state_path)
            handler.run(page, credentials=credentials, refresh=refresh)
