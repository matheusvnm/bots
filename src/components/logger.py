"""
Application logger setup using loguru.

Call configure_logging() once at startup (run.py).
All other modules do:  from loguru import logger

Session context is bound via logger.contextualize() in run.py,
making bot / user_id / session_id available on every log record.
"""

import sys
from pathlib import Path

from loguru import logger


# Stdout/file format — uses extra fields set by logger.configure() defaults
# and overridden per-session via logger.contextualize()
_APP_FMT = (
    "{time:YYYY-MM-DDTHH:mm:ss} | {level:<8} | "
    "{extra[bot]}:{extra[user_id]}:{extra[session_id]} | "
    "{name} | {message}"
)

# Network sink format — leaner, no level/name noise
_NET_FMT = (
    "{time:YYYY-MM-DDTHH:mm:ss} | "
    "{extra[bot]}:{extra[user_id]}:{extra[session_id]} | "
    "{message}"
)


def configure_logging() -> None:
    """Configure loguru sinks. Idempotent — safe to call more than once."""
    logger.remove()

    # Set defaults so the format works even before contextualize() is entered
    logger.configure(extra={"bot": "-", "user_id": "-", "session_id": "-"})

    # Stdout — colored, all app records
    logger.add(
        sys.stdout,
        format=_APP_FMT,
        level="INFO",
        colorize=True,
        filter=lambda r: not r["extra"].get("network_debug", False),
    )

    Path("logs").mkdir(parents=True, exist_ok=True)

    # Bot log file — app records only
    logger.add(
        "logs/bot.log",
        format=_APP_FMT,
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
        filter=lambda r: not r["extra"].get("network_debug", False),
    )

    # Network debug file — network_debug=True records only
    logger.add(
        "logs/network_debug.log",
        format=_NET_FMT,
        level="DEBUG",
        rotation="50 MB",
        retention="3 days",
        filter=lambda r: r["extra"].get("network_debug", False),
    )
