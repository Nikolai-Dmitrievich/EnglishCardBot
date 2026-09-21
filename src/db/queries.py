"""
Database query layer for the English Card Bot.

This module provides repository-style classes for performing CRUD operations
on users, words, and user-specific translations using SQLAlchemy 2.0
asynchronous sessions.
"""

from typing import Any

from sqlalchemy import delete, func, literal_column, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from .models import User, UserWord, Word


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
        await session.commit()
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
        result: CursorResult[Any] = await session.execute(
            delete(UserWord).where(
                UserWord.user_id == user.id,
                UserWord.word_user == word_user,
            )
        )
        await session.commit()
        return result.rowcount > 0  # type: ignore[attr-defined]

    @staticmethod
    async def get_random_word_for_user(
        session: AsyncSession,
        user: User,
    ) -> tuple[str | None, str | None, bool]:
        """
        Fetch a random word for the user's training session.

        The selection prioritizes the user's custom words first, then
        falls back to the common dictionary. Uses PostgreSQL's
        ``RANDOM()`` function for uniform distribution.

        Args:
            session: The active asynchronous SQLAlchemy session.
            user: The User for whom the word is selected.

        Returns:
            A tuple of (word, translation, is_user_word). Returns
            (None, None, False) if no words are available.
        """
        user_words_q: Any = select(
            UserWord.word_user.label("word"),
            UserWord.translation_user.label("translation"),
            literal_column("TRUE").label("is_user_word"),
        ).where(UserWord.user_id == user.id)

        common_words_q: Any = select(
            Word.word.label("word"),
            Word.translation.label("translation"),
            literal_column("FALSE").label("is_user_word"),
        )

        combined_q = user_words_q.union_all(common_words_q).subquery()
        stmt = select(combined_q).order_by(func.random()).limit(1)

        result = await session.execute(stmt)
        row = result.one_or_none()

        if row is None:
            return None, None, False
        return row.word, row.translation, row.is_user_word

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
        randomized using PostgreSQL's ``RANDOM()`` function.

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
            await session.commit()

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
