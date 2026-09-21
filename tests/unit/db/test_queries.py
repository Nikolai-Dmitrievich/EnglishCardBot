"""
Unit tests for the database query layer.

This module verifies the correctness of CRUD operations, random word selection,
wrong translation generation, and dictionary seeding within the repository layer,
ensuring compatibility across different SQL dialects (e.g., SQLite for testing,
PostgreSQL for production).
"""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Word
from src.db.queries import UserQueries, WordQueries


class TestUserQueries:
    """Tests for user-related database operations."""

    async def test_get_user_returns_none_for_unknown(
        self, session: AsyncSession
    ) -> None:
        """Verify that fetching a non-existent user returns None."""
        assert await UserQueries.get_user(session, "404") is None

    async def test_create_user_persists_and_returns_id(
        self, session: AsyncSession
    ) -> None:
        """Verify that creating a user persists the record and generates an ID."""
        user = await UserQueries.create_user(session, "100", "Nikolai")

        assert user.id is not None
        assert user.telegram_username == "Nikolai"

    async def test_get_or_create_is_idempotent(self, session: AsyncSession) -> None:
        """Verify that get_or_create returns the same user instance on subsequent calls."""
        first = await UserQueries.get_or_create_user(session, "100", "Nikolai")
        second = await UserQueries.get_or_create_user(session, "100", "Nikolai")

        assert first.id == second.id

    async def test_get_or_create_distinguishes_users(
        self, session: AsyncSession
    ) -> None:
        """Verify that get_or_create creates distinct records for different Telegram IDs."""
        first = await UserQueries.get_or_create_user(session, "100")
        second = await UserQueries.get_or_create_user(session, "200")

        assert first.id != second.id

    async def test_update_username(self, session: AsyncSession) -> None:
        """Verify that a user's display name can be successfully updated."""
        user = await UserQueries.create_user(session, "100", "old")

        updated = await UserQueries.update_username(session, user, "new")

        assert updated.telegram_username == "new"


class TestUserWords:
    """Tests for user-specific word management."""

    async def test_add_user_word(self, session: AsyncSession) -> None:
        """Verify that a new custom word is successfully added to the user's dictionary."""
        user = await UserQueries.create_user(session, "100")

        assert await WordQueries.add_user_word(session, user, "массив", "array") is True

        words = await WordQueries.get_user_words(session, user)
        assert [(w.word_user, w.translation_user) for w in words] == [
            ("массив", "array")
        ]

    async def test_add_duplicate_word_is_rejected(self, session: AsyncSession) -> None:
        """Verify that attempting to add a duplicate word for the same user is rejected."""
        user = await UserQueries.create_user(session, "100")
        await WordQueries.add_user_word(session, user, "массив", "array")

        assert (
            await WordQueries.add_user_word(session, user, "массив", "список") is False
        )

        words = await WordQueries.get_user_words(session, user)
        assert len(words) == 1
        assert words[0].translation_user == "array"

    async def test_same_word_allowed_for_different_users(
        self, session: AsyncSession
    ) -> None:
        """Verify that different users can add the same source word with different translations."""
        first = await UserQueries.create_user(session, "100")
        second = await UserQueries.create_user(session, "200")

        assert (
            await WordQueries.add_user_word(session, first, "массив", "array") is True
        )
        assert (
            await WordQueries.add_user_word(session, second, "массив", "vector") is True
        )

    async def test_delete_user_word(self, session: AsyncSession) -> None:
        """Verify that a user can successfully delete their own custom word."""
        user = await UserQueries.create_user(session, "100")
        await WordQueries.add_user_word(session, user, "массив", "array")

        assert await WordQueries.delete_user_word(session, user, "массив") is True
        assert await WordQueries.get_user_words(session, user) == []

    async def test_delete_missing_word_returns_false(
        self, session: AsyncSession
    ) -> None:
        """Verify that attempting to delete a non-existent word returns False."""
        user = await UserQueries.create_user(session, "100")

        assert await WordQueries.delete_user_word(session, user, "нет такого") is False

    async def test_delete_word_of_other_user_returns_false(
        self, session: AsyncSession
    ) -> None:
        """Verify that a user cannot delete a word belonging to another user."""
        first = await UserQueries.create_user(session, "100")
        second = await UserQueries.create_user(session, "200")
        await WordQueries.add_user_word(session, first, "массив", "array")

        assert await WordQueries.delete_user_word(session, second, "массив") is False
        assert len(await WordQueries.get_user_words(session, first)) == 1


class TestGetRandomWord:
    """Tests for retrieving random words for quiz sessions."""

    async def test_returns_none_when_no_words(self, session: AsyncSession) -> None:
        """Verify that requesting a random word from an empty database returns None."""
        user = await UserQueries.create_user(session, "100")

        assert await WordQueries.get_random_word_for_user(session, user) == (
            None,
            None,
            False,
        )

    async def test_prefers_nothing_but_common_words(
        self, session: AsyncSession, common_words: Any
    ) -> None:
        """Verify that common words are returned when the user has no custom words."""
        user = await UserQueries.create_user(session, "100")

        word, translation, is_user_word = await WordQueries.get_random_word_for_user(
            session, user
        )

        assert (word, translation) in common_words
        assert is_user_word is False

    async def test_user_word_is_marked(self, session: AsyncSession) -> None:
        """Verify that a custom word is correctly flagged as a user word."""
        user = await UserQueries.create_user(session, "100")
        await WordQueries.add_user_word(session, user, "массив", "array")

        word, translation, is_user_word = await WordQueries.get_random_word_for_user(
            session, user
        )

        assert (word, translation) == ("массив", "array")
        assert is_user_word is True

    async def test_mixes_user_and_common_words(
        self, session: AsyncSession, common_words: Any
    ) -> None:
        """
        Verify that user and common words are mixed correctly in random selection.

        This acts as a regression test for PostgreSQL's
        'invalid UNION/ORDER BY clause' error. Since PostgreSQL
        prohibits ORDER BY with functions at the UNION level,
        the query wraps the union in a subquery. While this
        specific error does not occur in SQLite, this test
        ensures the query executes successfully across dialects.
        """
        user = await UserQueries.create_user(session, "100")
        await WordQueries.add_user_word(session, user, "массив", "array")
        expected = {("массив", "array")} | set(common_words)

        seen = set()
        for _ in range(60):
            word, translation, _ = await WordQueries.get_random_word_for_user(
                session, user
            )
            seen.add((word, translation))

        assert seen <= expected
        # The user's custom word must appear in the selection at least once.
        assert ("массив", "array") in seen

    async def test_only_one_row_returned(
        self, session: AsyncSession, common_words: Any
    ) -> None:
        """Verify that the query strictly returns only a single row."""
        user = await UserQueries.create_user(session, "100")

        # one_or_none will raise MultipleResultsFound if limit(1) fails,
        # so the successful return of this call inherently validates the limit.
        word, _, _ = await WordQueries.get_random_word_for_user(session, user)
        assert word is not None


class TestGetWrongTranslations:
    """Tests for generating incorrect translation options for quizzes."""

    async def test_excludes_correct_translation(
        self, session: AsyncSession, common_words: Any
    ) -> None:
        """Verify that the correct translation is never included in the wrong options."""
        user = await UserQueries.create_user(session, "100")

        wrong = await WordQueries.get_wrong_translations(
            session, user, "computer", limit=3
        )

        assert "computer" not in wrong
        assert len(wrong) == 3

    async def test_respects_limit(
        self, session: AsyncSession, common_words: Any
    ) -> None:
        """Verify that the returned list of wrong options strictly respects the limit parameter."""
        user = await UserQueries.create_user(session, "100")

        assert (
            len(
                await WordQueries.get_wrong_translations(
                    session, user, "computer", limit=2
                )
            )
            == 2
        )

    async def test_returns_empty_when_nothing_to_offer(
        self, session: AsyncSession
    ) -> None:
        """Verify that an empty list is returned when no alternative translations exist."""
        user = await UserQueries.create_user(session, "100")

        assert await WordQueries.get_wrong_translations(session, user, "computer") == []

    async def test_deduplicates_across_sources(
        self, session: AsyncSession, common_words: Any
    ) -> None:
        """
        Verify that UNION (not UNION ALL) correctly deduplicates
        translations originating from both the user and common dictionaries.
        """
        user = await UserQueries.create_user(session, "100")
        await WordQueries.add_user_word(session, user, "твой компьютер", "computer")

        wrong = await WordQueries.get_wrong_translations(
            session, user, "network", limit=10
        )

        assert len(wrong) == len(set(wrong))
        assert "computer" in wrong


class TestPopulateCommonWords:
    """Tests for seeding the common dictionary."""

    async def test_fills_dictionary(
        self, session: AsyncSession, tech_words: Any
    ) -> None:
        """Verify that the initial seeding process adds all provided words."""
        added = await WordQueries.populate_common_words(session, tech_words)

        assert added == len(tech_words)
        total = await session.scalar(select(func.count()).select_from(Word))
        assert total == len(tech_words)

    async def test_is_idempotent(self, session: AsyncSession, tech_words: Any) -> None:
        """Verify that running the seeding process multiple times does not create duplicates."""
        await WordQueries.populate_common_words(session, tech_words)

        assert await WordQueries.populate_common_words(session, tech_words) == 0
        total = await session.scalar(select(func.count()).select_from(Word))
        assert total == len(tech_words)

    async def test_skips_existing_and_adds_new(self, session: AsyncSession) -> None:
        """Verify that existing words are skipped while new words are successfully added."""
        session.add(Word(word="компьютер", translation="computer"))
        await session.commit()

        added = await WordQueries.populate_common_words(
            session, [("компьютер", "ЭВМ"), ("сеть", "network")]
        )

        assert added == 1
        computer = await session.scalar(select(Word).where(Word.word == "компьютер"))
        assert computer.translation == "computer"
