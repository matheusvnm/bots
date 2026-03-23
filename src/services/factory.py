from typing import Any

from services.bots import AbstractBot, CoinbaseBot, KrakenBot


class BotFactory:
    _REGISTRY: dict[str, AbstractBot] = {
        "kraken": KrakenBot,
        "coinbase": CoinbaseBot,
    }

    @classmethod
    def create(cls, bot_name: str, **kwargs: dict[str, Any]) -> AbstractBot:
        bot_cls = cls._REGISTRY.get(bot_name)
        if bot_cls is None:
            available = ", ".join(cls._REGISTRY.keys())
            raise ValueError(f"Unknown bot {bot_name!r}. Available bots: {available}")
        return bot_cls(**kwargs)
