"""
Unit tests for the SQLAlchemy model layer.

These tests validate schema-level guarantees (constraints, cascades, server
defaults) that the repository layer relies on but does not enforce itself.
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.models import User, UserWord, Word


class TestTimestamps:
    """Tests for the server-side timestamp columns."""

    async def test_created_at_is_populated(self, session: AsyncSession) -> None:
        user = User(telegram_id="100")
        session.add(user)
        await session.commit()

        assert user.created_at is not None
        assert user.updated_at is not None

    async def test_updated_at_advances_on_update(self, session: AsyncSession) -> None:
        user = User(telegram_id="100", telegram_username="old")
        session.add(user)
        await session.commit()

        user.telegram_username = "new"
        await session.commit()

        refreshed = await session.scalar(
            select(User)
            .where(User.telegram_id == "100")
            .execution_options(populate_existing=True)
        )
        assert refreshed is not None
        assert refreshed.updated_at >= refreshed.created_at


class TestConstraints:
    """Tests for uniqueness constraints protecting the data model."""

    async def test_duplicate_telegram_id_is_rejected(
        self, session: AsyncSession
    ) -> None:
        session.add(User(telegram_id="100"))
        await session.flush()
        session.add(User(telegram_id="100"))

        with pytest.raises(IntegrityError):
            await session.flush()

    async def test_duplicate_common_word_is_rejected(
        self, session: AsyncSession
    ) -> None:
        session.add(Word(word="код", translation="code"))
        await session.flush()
        session.add(Word(word="код", translation="код"))

        with pytest.raises(IntegrityError):
            await session.flush()

    async def test_same_user_word_pair_is_rejected(self, session: AsyncSession) -> None:
        user = User(telegram_id="100")
        session.add(user)
        await session.flush()
        session.add_all(
            [
                UserWord(user_id=user.id, word_user="массив", translation_user="array"),
                UserWord(user_id=user.id, word_user="массив", translation_user="list"),
            ]
        )

        with pytest.raises(IntegrityError):
            await session.flush()

    async def test_translation_may_be_shared(self, session: AsyncSession) -> None:
        """Different source words legitimately share a single translation."""
        session.add_all(
            [
                Word(word="код", translation="code"),
                Word(word="исходник", translation="code"),
            ]
        )
        await session.flush()

        total = await session.scalar(
            select(func.count()).select_from(Word).where(Word.translation == "code")
        )
        assert total == 2


class TestRelationships:
    """Tests for the user-to-words relationship and its cascade rules."""

    async def test_words_are_reachable_from_user(self, session: AsyncSession) -> None:
        user = User(telegram_id="100")
        session.add(user)
        await session.flush()
        session.add(
            UserWord(user_id=user.id, word_user="массив", translation_user="array")
        )
        await session.commit()

        loaded = await session.scalar(
            select(User).where(User.id == user.id).options(selectinload(User.words))
        )
        assert loaded is not None
        assert [w.word_user for w in loaded.words] == ["массив"]

    async def test_deleting_user_removes_their_words(
        self, session: AsyncSession
    ) -> None:
        """
        Guard against orphaned rows.

        ``User.words`` declares ``cascade="all, delete-orphan"``, so removing a
        user must also remove their custom dictionary entries.
        """
        user = User(telegram_id="100")
        session.add(user)
        await session.flush()
        session.add_all(
            [
                UserWord(user_id=user.id, word_user="массив", translation_user="array"),
                UserWord(user_id=user.id, word_user="сеть", translation_user="network"),
            ]
        )
        await session.commit()

        loaded = await session.scalar(
            select(User).where(User.id == user.id).options(selectinload(User.words))
        )
        assert loaded is not None
        await session.delete(loaded)
        await session.commit()

        remaining = await session.scalar(select(func.count()).select_from(UserWord))
        assert remaining == 0

    async def test_deleting_user_keeps_common_words(
        self, session: AsyncSession
    ) -> None:
        """The shared dictionary is independent of any single user."""
        user = User(telegram_id="100")
        session.add(user)
        session.add(Word(word="код", translation="code"))
        await session.commit()

        loaded = await session.scalar(select(User).where(User.id == user.id))
        assert loaded is not None
        await session.delete(loaded)
        await session.commit()

        assert await session.scalar(select(func.count()).select_from(Word)) == 1


class TestOptionalFields:
    """Tests for nullable columns."""

    async def test_username_is_optional(self, session: AsyncSession) -> None:
        user = User(telegram_id="100")
        session.add(user)
        await session.commit()

        assert user.telegram_username is None
