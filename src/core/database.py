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
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.core.config import get_settings
from src.db.queries import UserQueries

_engine: AsyncEngine | None = None
_session_maker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """
    Build the asynchronous engine on first use and cache it afterwards.

    Deferring construction until the engine is actually requested keeps this
    module importable without a valid configuration, so a broken ``.env``
    surfaces as a clear startup error instead of an import-time crash.
    """
    global _engine

    if _engine is None:
        cfg = get_settings().db

        connect_args: dict[str, Any] = {}
        if cfg.engine.command_timeout_seconds > 0:
            connect_args["command_timeout"] = cfg.engine.command_timeout_seconds
        if cfg.engine.statement_timeout_seconds > 0:
            connect_args["server_settings"] = {
                "statement_timeout": str(cfg.engine.statement_timeout_seconds * 1000)
            }

        _engine = create_async_engine(
            url=cfg.db_url,
            echo=cfg.engine.echo,
            pool_pre_ping=cfg.engine.pool_pre_ping,
            pool_recycle=cfg.engine.pool_recycle,
            pool_size=cfg.engine.pool_size,
            max_overflow=cfg.engine.max_overflow,
            connect_args=connect_args,
        )

    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the cached session factory, creating it on first use."""
    global _session_maker

    if _session_maker is None:
        _session_maker = async_sessionmaker[AsyncSession](
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=get_settings().db.session.expire_on_commit,
        )

    return _session_maker


def session_factory() -> AsyncSession:
    """
    Convenience callable that keeps the historical ``session_factory()`` API.

    Exposed as a plain function so importing this module no longer requires a
    ready configuration; every call resolves the lazily built factory.
    """
    return get_session_factory()()


class DatabaseMiddleware(BaseMiddleware):
    """
    Aiogram middleware for injecting SQLAlchemy sessions and user instances.

    This middleware ensures that every handler execution has an active
    database session. It also proactively fetches or creates the user
    record, making it available to handlers via dependency injection.

    The handler call defines the transaction boundary: changes are committed
    only after a successful return, and any exception rolls the session back
    so a partially applied update can never reach the database.
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

            try:
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
            except Exception:
                await session.rollback()
                raise
