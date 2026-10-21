"""
Database query layer for the English Card Bot.

This module provides repository-style classes for performing CRUD operations
on users, words, and user-specific translations using SQLAlchemy 2.0
asynchronous sessions.
"""

from datetime import datetime
from typing import Any, cast

from sqlalchemy import delete, false, func, select, true
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from .models import User, UserWord, Word, WordError


class UserQueries:
    """Repository for all user-related database operations."""

    @staticmethod
    async def get_user(session: AsyncSession, telegram_id: str) -> User | None:
        """
        Fetch a user by their Telegram ID.

        Args:
            session: The active asynchronous SQLAlchemy session.
            telegram_id: The unique Telegram identifier of the user.

        Returns:
            The User instance if found, otherwise None.
        """
        result = await session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def create_user(
        session: AsyncSession,
        telegram_id: str,
        username: str | None = None,
    ) -> User:
        """
        Create a new user record in the database.

        Args:
            session: The active asynchronous SQLAlchemy session.
            telegram_id: The unique Telegram identifier of the user.
            username: The optional Telegram display name.

        Returns:
            The newly created User instance with a generated ID.
        """
        user = User(
            telegram_id=telegram_id,
            telegram_username=username,
        )
        session.add(user)
        await session.flush()
        return user

    @staticmethod
    async def get_or_create_user(
        session: AsyncSession,
        telegram_id: str,
        username: str | None = None,
    ) -> User:
        """
        Retrieve an existing user or create a new one if not found.

        Args:
            session: The active asynchronous SQLAlchemy session.
            telegram_id: The unique Telegram identifier of the user.
            username: The optional Telegram display name.

        Returns:
            The existing or newly created User instance.
        """
        user = await UserQueries.get_user(session=session, telegram_id=telegram_id)
        if user is None:
            user = await UserQueries.create_user(
                session=session,
                telegram_id=telegram_id,
                username=username,
            )
        return user

    @staticmethod
    async def update_username(
        session: AsyncSession,
        user: User,
        username: str | None,
    ) -> User:
        """
        Update the display name of an existing user.

        Args:
            session: The active asynchronous SQLAlchemy session.
            user: The User instance to update.
            username: The new Telegram display name.

        Returns:
            The updated User instance.
        """
        user.telegram_username = username
        await session.flush()
        return user


class WordQueries:
    """Repository for word-related database operations."""

    @staticmethod
    async def get_user_words(session: AsyncSession, user: User) -> list[UserWord]:
        """
        Retrieve all custom words added by a specific user.

        Args:
            session: The active asynchronous SQLAlchemy session.
            user: The User whose words should be fetched.

        Returns:
            A list of UserWord instances belonging to the user.
        """
        result = await session.execute(
            select(UserWord).where(UserWord.user_id == user.id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def add_user_word(
        session: AsyncSession,
        user: User,
        word_user: str,
        translation_user: str,
    ) -> bool:
        """
        Add a new custom word for a user if it does not already exist.

        Args:
            session: The active asynchronous SQLAlchemy session.
            user: The User who owns the word.
            word_user: The word in the source language.
            translation_user: The translation in the target language.

        Returns:
            True if the word was added, False if it already exists.
        """
        result = await session.execute(
            select(UserWord).where(
                UserWord.user_id == user.id,
                UserWord.word_user == word_user,
            )
        )
        if result.scalar_one_or_none() is not None:
            return False

        new_word = UserWord(
            user_id=user.id,
            word_user=word_user,
            translation_user=translation_user,
        )
        session.add(new_word)
        await session.flush()
        return True

    @staticmethod
    async def delete_user_word(
        session: AsyncSession,
        user: User,
        word_user: str,
    ) -> bool:
        """
        Delete a custom word belonging to a specific user.

        Args:
            session: The active asynchronous SQLAlchemy session.
            user: The User who owns the word.
            word_user: The word to be deleted.

        Returns:
            True if the word was deleted, False if it was not found.
        """
        # A DML statement yields a CursorResult, which is the only result
        # exposing rowcount; session.execute is typed as returning Result.
        result = cast(
            "CursorResult[Any]",
            await session.execute(
                delete(UserWord).where(
                    UserWord.user_id == user.id,
                    UserWord.word_user == word_user,
                )
            ),
        )
        return result.rowcount > 0

    @staticmethod
    async def get_random_word_for_user(
        session: AsyncSession,
        user: User,
    ) -> tuple[str | None, str | None, bool]:
        """
        Fetch a random word for the user's training session.

        The selection prioritizes the user's custom words first, then
        falls back to the common dictionary. Uses ``RANDOM()`` for
        uniform distribution across the combined pool.

        Args:
            session: The active asynchronous SQLAlchemy session.
            user: The User for whom the word is selected.

        Returns:
            A tuple of (word, translation, is_user_word). Returns
            (None, None, False) if no words are available.
        """
        # true()/false() rather than literal_column("TRUE"): the latter leaks the
        # raw driver representation (SQLite returns 1/0), breaking the bool return
        # type contract. SQLAlchemy renders these booleans per dialect.
        user_words_q: Any = select(
            UserWord.word_user.label("word"),
            UserWord.translation_user.label("translation"),
            true().label("is_user_word"),
        ).where(UserWord.user_id == user.id)

        common_words_q: Any = select(
            Word.word.label("word"),
            Word.translation.label("translation"),
            false().label("is_user_word"),
        )

        combined_q = user_words_q.union_all(common_words_q).subquery()
        stmt = select(combined_q).order_by(func.random()).limit(1)

        result = await session.execute(stmt)
        row = result.one_or_none()

        if row is None:
            return None, None, False
        return row.word, row.translation, bool(row.is_user_word)

    @staticmethod
    async def get_wrong_translations(
        session: AsyncSession,
        user: User,
        correct_translation: str,
        limit: int = 3,
    ) -> list[str]:
        """
        Fetch a list of incorrect translation options for a quiz.

        Combines translations from the user's custom words and the
        common dictionary, excluding the correct answer. Results are
        randomized using the dialect's ``RANDOM()`` function.

        Args:
            session: The active asynchronous SQLAlchemy session.
            user: The User whose custom translations should be considered.
            correct_translation: The correct translation to exclude.
            limit: Maximum number of wrong options to return.

        Returns:
            A list of incorrect translation strings.
        """
        user_translations_q = select(
            UserWord.translation_user.label("translation")
        ).where(
            UserWord.user_id == user.id,
            UserWord.translation_user != correct_translation,
        )

        common_translations_q = select(Word.translation.label("translation")).where(
            Word.translation != correct_translation,
        )

        combined_q = user_translations_q.union(common_translations_q).subquery()
        stmt = select(combined_q.c.translation).order_by(func.random()).limit(limit)

        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def _record_error(
        session: AsyncSession,
        user: User,
        user_word_id: int | None,
        common_word_id: int | None,
    ) -> None:
        """Persist one error row against already resolved dictionary ids."""
        session.add(
            WordError(
                user_id=user.id,
                user_word_id=user_word_id,
                common_word_id=common_word_id,
            )
        )
        await session.flush()

    @staticmethod
    async def record_error_for_word(
        session: AsyncSession,
        user: User,
        word: str,
    ) -> None:
        """
        Record a single error occurrence against the word the user missed.

        The word is resolved against the user's custom dictionary first and
        then against the common one, so the error row always points at a
        concrete dictionary entry. Keeping the resolved FK (rather than only
        the word text) is what lets ``/stat`` group errors per word and lets
        deleting a word clean up its error history.

        Args:
            session: The active asynchronous SQLAlchemy session.
            user: The User who made the error.
            word: The prompt word the user answered incorrectly.
        """
        user_word_id = await session.scalar(
            select(UserWord.id).where(
                UserWord.user_id == user.id,
                UserWord.word_user == word,
            )
        )
        common_word_id = (
            None
            if user_word_id is not None
            else await session.scalar(select(Word.id).where(Word.word == word))
        )

        await WordQueries._record_error(
            session=session,
            user=user,
            user_word_id=user_word_id,
            common_word_id=common_word_id,
        )

    @staticmethod
    async def record_error_for_translation(
        session: AsyncSession,
        user: User,
        translation: str,
    ) -> None:
        """
        Record an error identified by the English side of a word pair.

        The AI quiz asks for an English word to fill a blank, so the option
        the user missed is a translation, not a dictionary headword. Resolving
        it through ``translation_user``/``translation`` maps the miss back onto
        the same dictionary entries the plain quiz uses, which keeps ``/stat``
        and ``get_weak_words`` reporting one merged count per word regardless
        of which quiz produced the error.

        Args:
            session: The active asynchronous SQLAlchemy session.
            user: The User who made the error.
            translation: The English option the user failed to pick.
        """
        user_word_id = await session.scalar(
            select(UserWord.id).where(
                UserWord.user_id == user.id,
                UserWord.translation_user == translation,
            )
        )
        common_word_id = (
            None
            if user_word_id is not None
            else await session.scalar(
                select(Word.id).where(Word.translation == translation)
            )
        )

        await WordQueries._record_error(
            session=session,
            user=user,
            user_word_id=user_word_id,
            common_word_id=common_word_id,
        )

    @staticmethod
    async def get_weak_words(
        session: AsyncSession,
        user: User,
        limit: int = 10,
    ) -> list[dict]:
        """
        Retrieve the user's most error-prone words, sorted by error count.

        Only returns words that have been missed at least once.

        Args:
            session: The active asynchronous SQLAlchemy session.
            user: The User whose weak words should be fetched.
            limit: Maximum number of words to return.

        Returns:
            A list of dicts with 'user_word', 'translation', and 'error_count'.
        """
        subq = (
            select(WordError.user_word_id, func.count(WordError.id).label("cnt"))
            .where(WordError.user_id == user.id, WordError.user_word_id.isnot(None))
            .group_by(WordError.user_word_id)
            .subquery()
        )

        result = await session.execute(
            select(UserWord, subq.c.cnt)
            .join(subq, UserWord.id == subq.c.user_word_id)
            .where(UserWord.user_id == user.id, subq.c.cnt > 0)
            .order_by(subq.c.cnt.desc(), UserWord.created_at.desc())
            .limit(limit)
        )

        return [
            {
                "word_user": row[0].word_user,
                "translation_user": row[0].translation_user,
                "error_count": row[1],
            }
            for row in result.all()
        ]

    @staticmethod
    async def get_errors_by_period(
        session: AsyncSession,
        user: User,
        start_date: datetime,
        end_date: datetime,
    ) -> dict:
        """
        Get error statistics for a given date range.

        Args:
            session: The active asynchronous SQLAlchemy session.
            user: The User whose errors should be fetched.
            start_date: Start of the period (inclusive).
            end_date: End of the period (inclusive).

        Returns:
            Dict with 'total_errors', 'daily_errors', 'by_word'.
        """
        total = await session.execute(
            select(func.count(WordError.id)).where(
                WordError.user_id == user.id,
                WordError.error_date >= start_date,
                WordError.error_date < end_date,
            )
        )
        total_errors: int = total.scalar() or 0

        # Fetch raw error dates and group in Python for cross-dialect safety.
        # SQLAlchemy's func.date() is not available on PostgreSQL.
        raw_daily = await session.execute(
            select(WordError.error_date).where(
                WordError.user_id == user.id,
                WordError.error_date >= start_date,
                WordError.error_date < end_date,
            )
        )
        daily_errors: dict[str, int] = {}
        for row in raw_daily.scalars().all():
            day_key = row.date().isoformat()
            daily_errors[day_key] = daily_errors.get(day_key, 0) + 1

        by_word_list: list[dict] = []

        # Errors from user's custom words (user_word_id is set).
        by_word_result = await session.execute(
            select(
                UserWord.word_user,
                UserWord.translation_user,
                func.count(WordError.id).label("cnt"),
            )
            .join(WordError, UserWord.id == WordError.user_word_id)
            .where(
                WordError.user_id == user.id,
                WordError.error_date >= start_date,
                WordError.error_date < end_date,
                WordError.user_word_id.isnot(None),
            )
            .group_by(UserWord.id, UserWord.word_user, UserWord.translation_user)
            .order_by(func.count(WordError.id).desc())
        )
        by_word_list.extend(
            {
                "word": row.word_user,
                "translation": row.translation_user,
                "errors": row.cnt,
            }
            for row in by_word_result.all()
        )

        # Errors from the common dictionary (only common_word_id is set).
        # This covers mistakes recorded via ``record_error_for_translation``
        # during the AI quiz, where the user missed an English option.
        common_word_result = await session.execute(
            select(
                Word.word.label("word"),
                Word.translation.label("translation"),
                func.count(WordError.id).label("cnt"),
            )
            .join(WordError, Word.id == WordError.common_word_id)
            .where(
                WordError.user_id == user.id,
                WordError.error_date >= start_date,
                WordError.error_date < end_date,
                WordError.user_word_id.is_(None),
                WordError.common_word_id.isnot(None),
            )
            .group_by(Word.id, Word.word, Word.translation)
            .order_by(func.count(WordError.id).desc())
        )
        by_word_list.extend(
            {
                "word": row.word,
                "translation": row.translation,
                "errors": row.cnt,
            }
            for row in common_word_result.all()
        )

        by_word_list.sort(key=lambda x: x["errors"], reverse=True)

        return {
            "total_errors": total_errors,
            "daily_errors": daily_errors,
            "by_word": by_word_list,
        }

    @staticmethod
    async def get_user_word_id_by_text(
        session: AsyncSession,
        user: User,
        word_user: str,
    ) -> int | None:
        """Get the DB id of a UserWord by its text for a given user."""
        result = await session.execute(
            select(UserWord.id).where(
                UserWord.user_id == user.id, UserWord.word_user == word_user
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def populate_common_words(
        session: AsyncSession,
        tech_words_data: list[tuple[str, str]],
    ) -> int:
        """
        Populate the common dictionary with initial word pairs.

        This operation is idempotent: existing words are skipped,
        and only new entries are inserted.

        Args:
            session: The active asynchronous SQLAlchemy session.
            tech_words_data: A list of (word, translation) tuples.

        Returns:
            The number of newly added words.
        """
        result = await session.execute(select(Word.word))
        existing_words = set(result.scalars().all())

        added_count = 0
        for word, translation in tech_words_data:
            if word not in existing_words:
                session.add(Word(word=word, translation=translation))
                added_count += 1

        if added_count > 0:
            await session.flush()

        return added_count


TECH_WORDS_DATA: list[tuple[str, str]] = [
    ("компьютер", "computer"),
    ("программное обеспечение", "software"),
    ("аппаратное обеспечение", "hardware"),
    ("сеть", "network"),
    ("база данных", "database"),
    ("сервер", "server"),
    ("клиент", "client"),
    ("алгоритм", "algorithm"),
    ("отлаживать", "debug"),
    ("код", "code"),
    ("интерфейс", "interface"),
    ("протокол", "protocol"),
    ("шифрование", "encryption"),
    ("межсетевой экран", "firewall"),
    ("облако", "cloud"),
    ("виртуализация", "virtualization"),
    ("компилятор", "compiler"),
    ("фреймворк", "framework"),
    ("репозиторий", "repository"),
]
