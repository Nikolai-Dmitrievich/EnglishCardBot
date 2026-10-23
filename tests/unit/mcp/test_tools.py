"""Unit tests for the MCP tools layer."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import UserWord, WordError
from src.db.queries import UserQueries
from src.mcp.tools import (
    configure_session_pool,
    get_recent_words,
    get_user_stats,
    get_weak_words,
)


@pytest.fixture
def patched_session_factory(session_factory):
    """Point the MCP tools at the in-memory test database."""
    configure_session_pool(session_factory)
    yield session_factory
    configure_session_pool(None)


class TestGetRecentWords:
    """Tests for the get_recent_words MCP tool."""

    async def test_returns_empty_for_unknown_user(
        self, patched_session_factory
    ) -> None:
        """Verify that a non-existent user returns an empty list."""
        result = await get_recent_words(telegram_id="999", limit=10)
        assert result == []

    async def test_returns_recent_words_sorted_by_newest(
        self, session: AsyncSession, patched_session_factory
    ) -> None:
        """Verify that recent words are returned in descending order."""
        user = await UserQueries.create_user(session, "100", "TestUser")
        await session.flush()

        # created_at is set explicitly: SQLite's CURRENT_TIMESTAMP has
        # second-level precision, so rows inserted in one transaction
        # would otherwise share a timestamp and sort non-deterministically.
        base = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)
        words = [
            ("first", "первый", base),
            ("second", "второй", base + timedelta(minutes=1)),
            ("third", "третий", base + timedelta(minutes=2)),
        ]
        for word_user, translation, created_at in words:
            session.add(
                UserWord(
                    user_id=user.id,
                    word_user=word_user,
                    translation_user=translation,
                    created_at=created_at,
                )
            )
        await session.commit()

        result = await get_recent_words(telegram_id="100", limit=10)

        assert len(result) == 3
        assert result[0] == {"word": "third", "translation": "третий"}
        assert result[1] == {"word": "second", "translation": "второй"}
        assert result[2] == {"word": "first", "translation": "первый"}

    async def test_respects_limit(
        self, session: AsyncSession, patched_session_factory
    ) -> None:
        """Verify that the limit parameter restricts the number of results."""
        user = await UserQueries.create_user(session, "100", "TestUser")
        await session.flush()

        for i in range(5):
            session.add(
                UserWord(
                    user_id=user.id,
                    word_user=f"word{i}",
                    translation_user=f"translation{i}",
                )
            )
        await session.commit()

        result = await get_recent_words(telegram_id="100", limit=2)

        assert len(result) == 2

    async def test_empty_list_when_user_has_no_words(
        self, session: AsyncSession, patched_session_factory
    ) -> None:
        """Verify that a user with no custom words returns an empty list."""
        await UserQueries.create_user(session, "100", "TestUser")
        await session.commit()

        result = await get_recent_words(telegram_id="100", limit=10)
        assert result == []


class TestGetUserStats:
    """Tests for the get_user_stats MCP tool."""

    async def test_returns_zero_for_unknown_user(self, patched_session_factory) -> None:
        """Verify that a non-existent user returns zero counts."""
        result = await get_user_stats(telegram_id="999")
        assert result == {"user_words_count": 0, "dictionary_size": 0}

    async def test_returns_correct_counts(
        self,
        session: AsyncSession,
        patched_session_factory,
        common_words: list[tuple[str, str]],
    ) -> None:
        """Verify that user word count and dictionary size are correct."""
        user = await UserQueries.create_user(session, "100", "TestUser")
        await session.flush()

        session.add(
            UserWord(user_id=user.id, word_user="custom", translation_user="кастомный")
        )
        await session.commit()

        result = await get_user_stats(telegram_id="100")

        assert result["user_words_count"] == 1
        assert result["dictionary_size"] == len(common_words)


class TestGetWeakWords:
    """Tests for the get_weak_words MCP tool."""

    async def test_returns_empty_for_unknown_user(
        self, patched_session_factory
    ) -> None:
        """Verify that a non-existent user returns an empty list."""
        result = await get_weak_words(telegram_id="999", limit=5)
        assert result == []

    async def test_returns_empty_when_no_errors(
        self, session: AsyncSession, patched_session_factory
    ) -> None:
        """Verify that words with zero errors are not returned."""
        user = await UserQueries.create_user(session, "100", "TestUser")
        await session.flush()

        session.add(
            UserWord(user_id=user.id, word_user="word", translation_user="перевод")
        )
        await session.commit()

        result = await get_weak_words(telegram_id="100", limit=5)
        assert result == []

    async def test_returns_words_sorted_by_error_count(
        self, session: AsyncSession, patched_session_factory
    ) -> None:
        """Verify that weak words are sorted by error count descending."""
        user = await UserQueries.create_user(session, "100", "TestUser")
        await session.flush()

        words_data = [
            ("word_a", "a", 1),
            ("word_b", "b", 5),
            ("word_c", "c", 3),
        ]
        for word_user, translation, error_count in words_data:
            uw = UserWord(
                user_id=user.id,
                word_user=word_user,
                translation_user=translation,
            )
            session.add(uw)
            await session.flush()

            for _ in range(error_count):
                session.add(
                    WordError(
                        user_id=user.id,
                        user_word_id=uw.id,
                        error_date=datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC),
                    )
                )
        await session.commit()

        result = await get_weak_words(telegram_id="100", limit=5)

        assert len(result) == 3
        assert result[0]["error_count"] == 5
        assert result[1]["error_count"] == 3
        assert result[2]["error_count"] == 1

    async def test_respects_limit(
        self, session: AsyncSession, patched_session_factory
    ) -> None:
        """Verify that the limit parameter restricts the number of results."""
        user = await UserQueries.create_user(session, "100", "TestUser")
        await session.flush()

        for i in range(5):
            uw = UserWord(
                user_id=user.id,
                word_user=f"word{i}",
                translation_user=f"trans{i}",
            )
            session.add(uw)
            await session.flush()

            session.add(
                WordError(
                    user_id=user.id,
                    user_word_id=uw.id,
                    error_date=datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC),
                )
            )
        await session.commit()

        result = await get_weak_words(telegram_id="100", limit=2)

        assert len(result) == 2
        assert result[0]["error_count"] == 1
        assert result[1]["error_count"] == 1

    async def test_returns_word_and_translation(
        self, session: AsyncSession, patched_session_factory
    ) -> None:
        """Verify that each result contains word, translation, and error_count."""
        user = await UserQueries.create_user(session, "100", "TestUser")
        await session.flush()

        uw = UserWord(
            user_id=user.id,
            word_user="test_word",
            translation_user="test_translation",
        )
        session.add(uw)
        await session.flush()

        for _ in range(2):
            session.add(
                WordError(
                    user_id=user.id,
                    user_word_id=uw.id,
                    error_date=datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC),
                )
            )
        await session.commit()

        result = await get_weak_words(telegram_id="100", limit=5)

        assert len(result) == 1
        assert result[0]["word"] == "test_word"
        assert result[0]["translation"] == "test_translation"
        assert result[0]["error_count"] == 2
