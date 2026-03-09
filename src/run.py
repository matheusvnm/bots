"""
Bot runner entrypoint.

Usage:
    python src/run.py --bot kraken --action deposit
    python src/run.py --bot kraken --action withdraw --user 123456789
    python src/run.py --bot kraken --action deposit --refresh
"""

import argparse
import sys
import uuid
from pathlib import Path

from loguru import logger

from components.dtos import KrakenCredentials
from components.logger import configure_logging
from components.trace import PageTracer, TraceContext
from services.factory import BotFactory
from settings import Settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a bot")
    parser.add_argument(
        "--bot",
        required=True,
        help="Bot name (e.g. kraken)",
    )
    parser.add_argument(
        "--user",
        default="1",
        dest="user_identifier",
        help="User identifier — selects .context/{bot}/user/{id}/ (default: 1)",
    )
    parser.add_argument(
        "--action",
        required=True,
        choices=["deposit", "withdraw"],
        help="Action to perform after login",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        default=False,
        help="Ignore cached asset list and re-scrape from Kraken",
    )
    return parser.parse_args()


def run_bot() -> None:
    configure_logging()

    args = parse_args()
    settings = Settings()
    session_id = uuid.uuid4().hex
    
    ctx = TraceContext(bot=args.bot, 
                       user_id=args.user_identifier, 
                       session_id=session_id)

    tracer = PageTracer(ctx=ctx)
    bot = BotFactory.create(args.bot, tracer=tracer)

    state_file_path = settings.context_dir / args.bot / "user" / args.user_identifier / "state.json"
    credentials = KrakenCredentials(
        email=settings.user_email,
        password=settings.user_password,
        device_cookie_path=str(state_file_path)
    )

    with logger.contextualize(bot=ctx.bot, user_id=ctx.user_id, session_id=ctx.session_id):
        logger.info("Session started  session_id={}", session_id)
        try:
            bot.run(credentials, action=args.action, refresh=args.refresh)
            logger.info("Session ended successfully")
        except Exception as e:
            logger.error("Session failed: {}", e)
            raise


if __name__ == "__main__":
    try:
        run_bot()
    except Exception:
        logger.exception("An error occurred while running the bot")
        sys.exit(1)
