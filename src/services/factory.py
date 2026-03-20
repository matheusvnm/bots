from typing import Any

from services.bots import CoinbaseBot, KrakenBot


class BotFactory:
    _REGISTRY: dict[str, type] = {
        "kraken": KrakenBot,
        "coinbase": CoinbaseBot,
    }

    @classmethod
    def create(cls, bot_name: str, **kwargs: dict[str, Any]) -> Any:
        bot_cls = cls._REGISTRY.get(bot_name)
        if bot_cls is None:
            available = ", ".join(cls._REGISTRY.keys())
            raise ValueError(f"Unknown bot {bot_name!r}. Available bots: {available}")
        return bot_cls(**kwargs)
