"""
Database engine configuration, session management, and Aiogram middleware.

This module initializes the asynchronous SQLAlchemy engine with optimized
connection pooling and asyncpg-specific settings. It also provides a
middleware for seamless dependency injection of database sessions and
user instances into Aiogram handlers.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.core.config import settings
from src.db.queries import UserQueries

eng_cfg = settings.db.engine

connect_args: dict[str, Any] = {}

if eng_cfg.command_timeout_seconds > 0:
    connect_args["command_timeout"] = eng_cfg.command_timeout_seconds

if eng_cfg.statement_timeout_seconds > 0:
    connect_args["server_settings"] = {
        "statement_timeout": str(eng_cfg.statement_timeout_seconds * 1000)
    }

engine = create_async_engine(
    url=settings.db.db_url,
    echo=eng_cfg.echo,
    pool_pre_ping=eng_cfg.pool_pre_ping,
    pool_recycle=eng_cfg.pool_recycle,
    pool_size=eng_cfg.pool_size,
    max_overflow=eng_cfg.max_overflow,
    connect_args=connect_args or None,
)


session_cfg = settings.db.session

session_factory = async_sessionmaker[AsyncSession](
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=session_cfg.expire_on_commit,
)


class DatabaseMiddleware(BaseMiddleware):
    """
    Aiogram middleware for injecting SQLAlchemy sessions and user instances.

    This middleware ensures that every handler execution has an active
    database session. It also proactively fetches or creates the user
    record, making it available to handlers via dependency injection.
    """

    def __init__(self, session_pool: async_sessionmaker[AsyncSession]) -> None:
        self.session_pool = session_pool

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self.session_pool() as session:
            data["session"] = session

            event_from_user = data.get("event_from_user")
            if event_from_user is not None:
                data["user"] = await UserQueries.get_or_create_user(
                    session=session,
                    telegram_id=str(event_from_user.id),
                    username=event_from_user.first_name,
                )

            result = await handler(event, data)
            await session.commit()
            return result
