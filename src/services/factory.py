from typing import Any

from services.bots import KrakenBot


class BotFactory:
    _REGISTRY: dict[str, type] = {
        "kraken": KrakenBot,
    }

    @classmethod
    def create(cls, bot_name: str, **kwargs: dict[str, Any]) -> KrakenBot:
        bot_cls = cls._REGISTRY.get(bot_name)
        if bot_cls is None:
            available = ", ".join(cls._REGISTRY.keys())
            raise ValueError(f"Unknown bot {bot_name!r}. Available bots: {available}")
        return bot_cls(**kwargs)
