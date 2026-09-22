"""
Main entry point for the English Card Bot application.

This module initializes the database connection, configures the Aiogram
dispatcher, registers middlewares and routers, and starts the polling process.
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.exc import SQLAlchemyError

from src.core.config import settings
from src.core.database import DatabaseMiddleware, session_factory
from src.db.queries import TECH_WORDS_DATA, WordQueries
from src.handlers import common, quiz, words

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
            return await WordQueries.populate_common_words(session, TECH_WORDS_DATA)
        except SQLAlchemyError as e:
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
    dp.include_router(quiz.router)
    dp.include_router(words.router)

    added_count = await seed_initial_words()
    if added_count > 0:
        logger.info("Added %d new words to the common dictionary", added_count)
    else:
        logger.info("Common dictionary is already populated")

    logger.info("Bot started successfully")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user")
