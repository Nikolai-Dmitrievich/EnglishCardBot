"""
Unit tests for the application entry point helpers.

Only the seeding helper is covered here: starting polling requires a real
Telegram connection and is intentionally out of scope for the test suite.
"""

from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import src.main as bot_main
from src.db.models import Word


class _FailingSession:
    """Session stub that opens fine but fails on the first statement."""

    async def __aenter__(self) -> "_FailingSession":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def execute(self, *args: Any, **kwargs: Any) -> None:
        raise SQLAlchemyError("database is unavailable")


class TestSeedInitialWords:
    """Tests for the idempotent startup seeding of the common dictionary."""

    async def test_seeds_full_dictionary(
        self,
        monkeypatch: pytest.MonkeyPatch,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        monkeypatch.setattr(bot_main, "session_factory", session_factory)

        added = await bot_main.seed_initial_words()

        assert added == len(bot_main.TECH_WORDS_DATA)

    async def test_second_run_adds_nothing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        monkeypatch.setattr(bot_main, "session_factory", session_factory)

        await bot_main.seed_initial_words()
        added = await bot_main.seed_initial_words()

        assert added == 0

    async def test_seeded_words_are_persisted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        monkeypatch.setattr(bot_main, "session_factory", session_factory)

        await bot_main.seed_initial_words()

        async with session_factory() as verifier:
            total = await verifier.scalar(select(func.count()).select_from(Word))
            assert total == len(bot_main.TECH_WORDS_DATA)

    async def test_existing_partial_dictionary_is_completed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        session_factory: async_sessionmaker[AsyncSession],
        session: AsyncSession,
    ) -> None:
        monkeypatch.setattr(bot_main, "session_factory", session_factory)
        session.add(Word(word="компьютер", translation="computer"))
        await session.commit()

        added = await bot_main.seed_initial_words()

        assert added == len(bot_main.TECH_WORDS_DATA) - 1

    async def test_database_error_is_swallowed(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """
        Seeding must never abort startup.

        A database failure while reading or writing words is logged and reported
        as zero added words so that the bot can still start and serve content
        that is already present.
        """
        monkeypatch.setattr(bot_main, "session_factory", _FailingSession)

        added = await bot_main.seed_initial_words()

        assert added == 0
        assert "Database error while seeding" in caplog.text
