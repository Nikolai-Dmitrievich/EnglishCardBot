"""
SQLAlchemy database models for the English Card Bot.

This module defines the schema for users, common dictionary words,
and user-specific word translations using SQLAlchemy 2.0 declarative syntax.
"""

from datetime import datetime
from typing import Annotated

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""


# Reusable column type annotations for consistency across models
PrimaryKey = Annotated[int, mapped_column(primary_key=True, autoincrement=True)]

CreatedAt = Annotated[
    datetime,
    mapped_column(DateTime(timezone=True), server_default=func.now()),
]

UpdatedAt = Annotated[
    datetime,
    mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    ),
]


class User(Base):
    """Represents a Telegram user interacting with the bot."""

    __tablename__ = "users"

    id: Mapped[PrimaryKey]
    telegram_id: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    telegram_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    words: Mapped[list["UserWord"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class Word(Base):
    """Represents a word in the common dictionary."""

    __tablename__ = "words"

    id: Mapped[PrimaryKey]
    word: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    translation: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[CreatedAt]


class UserWord(Base):
    """Represents a custom word added by a specific user."""

    __tablename__ = "users_words"

    id: Mapped[PrimaryKey]
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    word_user: Mapped[str] = mapped_column(String(200), nullable=False)
    translation_user: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[CreatedAt]

    user: Mapped["User"] = relationship(back_populates="words")

    __table_args__ = (
        UniqueConstraint("user_id", "word_user", name="uq_user_word"),
        Index("idx_user_word", "user_id", "word_user"),
    )
