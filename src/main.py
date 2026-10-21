"""
Main entry point for the English Card Bot application.

This module initializes the database connection, configures the Aiogram
dispatcher, registers middlewares and routers, and starts the polling process.
"""

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.exc import SQLAlchemyError

from src.core.config import settings
from src.core.database import DatabaseMiddleware, session_factory
from src.db.queries import TECH_WORDS_DATA, WordQueries
from src.handlers import ai_quiz, common, quiz, stat, words
from src.mcp.tools import mcp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def seed_initial_words() -> int:
    """
    Populate the common dictionary with initial words on bot startup.

    This operation is idempotent and will not add duplicate entries.

    Returns:
        int: The number of newly added words.
    """
    async with session_factory() as session:
        try:
            count = await WordQueries.populate_common_words(session, TECH_WORDS_DATA)
            await session.commit()
            return count
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error("Database error while seeding initial words: %s", e)
            return 0


async def main() -> None:
    """
    Initialize and start the Telegram bot.

    Sets up the HTTP session, bot instance, dispatcher, middlewares,
    and routers. It also triggers the initial database seeding before
    starting the polling loop.
    """
    proxy_url = getattr(settings.bot, "proxy", None)

    if proxy_url:
        session = AiohttpSession(proxy=proxy_url)
        logger.info("Bot is running through proxy: %s", proxy_url)
    else:
        session = AiohttpSession()

    bot = Bot(token=settings.bot.token.get_secret_value(), session=session)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    dp.message.middleware(DatabaseMiddleware(session_factory))
    dp.callback_query.middleware(DatabaseMiddleware(session_factory))

    dp.include_router(common.router)
    # Registered ahead of the quiz routers: quiz.router binds a catch-all
    # StateFilter(waiting_for_quiz_answer) to plain text, which would otherwise
    # swallow /stat while a quiz question is pending.
    dp.include_router(stat.router)
    # Registered before quiz.router: its StateFilter(waiting_for_quiz_answer)
    # matches any text, so it would otherwise swallow /quiz_ai while a quiz
    # question is pending.
    dp.include_router(ai_quiz.router)
    dp.include_router(quiz.router)
    dp.include_router(words.router)

    added_count = await seed_initial_words()
    if added_count > 0:
        logger.info("Added %d new words to the common dictionary", added_count)
    else:
        logger.info("Common dictionary is already populated")

    logger.info("Bot started successfully")
    await dp.start_polling(bot)


def run_mcp_server() -> None:
    """
    Start the MCP server on the stdio transport.

    Started separately from the bot with ``uv run python -m src.main mcp``.
    Because the stdio transport owns stdout, log records are redirected to
    stderr to keep the protocol stream clean.
    """
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
    root_logger.addHandler(logging.StreamHandler(sys.stderr))

    logger.info("MCP server started on stdio transport")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "mcp":
        run_mcp_server()
    else:
        try:
            asyncio.run(main())
        except (KeyboardInterrupt, SystemExit):
            logger.info("Bot stopped by user")
