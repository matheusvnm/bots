from typing import Any

from services.bots import (
    AbstractBot,
    CoinbaseApiBot,
    CoinbaseBot,
    KrakenApiBot,
    KrakenBot,
)


class BotFactory:
    _REGISTRY: dict[str, AbstractBot] = {
        "kraken": KrakenBot,
        "kraken-api": KrakenApiBot,
        "coinbase": CoinbaseBot,
        "coinbase-api": CoinbaseApiBot,
    }

    @classmethod
    def create(cls, bot_name: str, **kwargs: dict[str, Any]) -> AbstractBot:
        bot_cls = cls._REGISTRY.get(bot_name)
        if bot_cls is None:
            available = ", ".join(cls._REGISTRY.keys())
            raise ValueError(f"Unknown bot {bot_name!r}. Available bots: {available}")
        return bot_cls(**kwargs)
