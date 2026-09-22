"""
Unit tests for the Aiogram database middleware.

These tests exercise ``DatabaseMiddleware`` directly (without a Dispatcher)
to verify session lifecycle, user auto-provisioning, and transaction
boundary behaviour around handler execution.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from aiogram.types import Chat, Message, TelegramObject
from aiogram.types import User as TgUser
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.database import DatabaseMiddleware
from src.db.models import User, Word


@pytest.fixture
def middleware(
    session_factory: async_sessionmaker[AsyncSession],
) -> DatabaseMiddleware:
    return DatabaseMiddleware(session_factory)


@pytest.fixture
def event() -> TelegramObject:
    return Message(
        message_id=1,
        date=datetime.now(tz=UTC),
        chat=Chat(id=111, type="private"),
    )


async def _noop_handler(event: TelegramObject, data: dict[str, Any]) -> str:
    return "handled"


def _tg_user(user_id: int = 111, first_name: str = "Nikolai") -> TgUser:
    return TgUser(id=user_id, is_bot=False, first_name=first_name)


class TestSessionInjection:
    """Tests for session provisioning and transaction handling."""

    async def test_injects_session_into_data(
        self,
        middleware: DatabaseMiddleware,
        event: TelegramObject,
    ) -> None:
        data: dict[str, Any] = {}

        await middleware(_noop_handler, event, data)

        assert isinstance(data["session"], AsyncSession)

    async def test_returns_handler_result(
        self,
        middleware: DatabaseMiddleware,
        event: TelegramObject,
    ) -> None:
        result = await middleware(_noop_handler, event, {})

        assert result == "handled"

    async def test_commits_changes_made_by_handler(
        self,
        middleware: DatabaseMiddleware,
        session_factory: async_sessionmaker[AsyncSession],
        event: TelegramObject,
    ) -> None:
        async def handler(event: TelegramObject, data: dict[str, Any]) -> None:
            data["session"].add(Word(word="кэш", translation="cache"))

        await middleware(handler, event, {})

        async with session_factory() as verifier:
            total = await verifier.scalar(select(func.count()).select_from(Word))
            assert total == 1

    async def test_handler_exception_discards_uncommitted_changes(
        self,
        middleware: DatabaseMiddleware,
        session_factory: async_sessionmaker[AsyncSession],
        event: TelegramObject,
    ) -> None:
        async def failing_handler(
            event: TelegramObject,
            data: dict[str, Any],
        ) -> None:
            data["session"].add(Word(word="кэш", translation="cache"))
            raise RuntimeError("handler crashed")

        with pytest.raises(RuntimeError):
            await middleware(failing_handler, event, {})

        async with session_factory() as verifier:
            total = await verifier.scalar(select(func.count()).select_from(Word))
            assert total == 0


class TestUserInjection:
    """Tests for automatic user provisioning from the incoming event."""

    async def test_creates_user_from_event(
        self,
        middleware: DatabaseMiddleware,
        session_factory: async_sessionmaker[AsyncSession],
        event: TelegramObject,
    ) -> None:
        data: dict[str, Any] = {"event_from_user": _tg_user()}

        await middleware(_noop_handler, event, data)

        assert isinstance(data["user"], User)
        assert data["user"].telegram_id == "111"
        assert data["user"].telegram_username == "Nikolai"

        async with session_factory() as verifier:
            stored = await verifier.scalar(select(User))
            assert stored is not None
            assert stored.telegram_id == "111"

    async def test_reuses_existing_user(
        self,
        middleware: DatabaseMiddleware,
        event: TelegramObject,
    ) -> None:
        first: dict[str, Any] = {"event_from_user": _tg_user()}
        second: dict[str, Any] = {"event_from_user": _tg_user()}

        await middleware(_noop_handler, event, first)
        await middleware(_noop_handler, event, second)

        assert first["user"].id == second["user"].id

    async def test_skips_injection_without_event_from_user(
        self,
        middleware: DatabaseMiddleware,
        session_factory: async_sessionmaker[AsyncSession],
        event: TelegramObject,
    ) -> None:
        data: dict[str, Any] = {}

        await middleware(_noop_handler, event, data)

        assert "user" not in data
        async with session_factory() as verifier:
            assert await verifier.scalar(select(func.count()).select_from(User)) == 0

    async def test_propagates_database_error(
        self,
        middleware: DatabaseMiddleware,
        event: TelegramObject,
    ) -> None:
        """A broken session must surface as an error rather than a silent pass."""

        class BrokenSession:
            async def __aenter__(self) -> "BrokenSession":
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

            async def execute(self, *args: Any, **kwargs: Any) -> None:
                raise SQLAlchemyError("connection lost")

            async def commit(self) -> None:
                return None

        broken_factory: Callable[[], BrokenSession] = BrokenSession
        broken_middleware = DatabaseMiddleware(broken_factory)  # type: ignore[arg-type]

        with pytest.raises(SQLAlchemyError):
            await broken_middleware(
                _noop_handler,
                event,
                {"event_from_user": _tg_user()},
            )
