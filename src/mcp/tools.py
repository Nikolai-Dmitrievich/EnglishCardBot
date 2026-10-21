"""MCP tools for EnglishCardBot — exposes bot data to external AI agents."""

from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field
from sqlalchemy import func, select

from src.core.database import session_factory
from src.db.models import UserWord, Word
from src.db.queries import UserQueries, WordQueries

mcp = FastMCP("EnglishCardBot")


@mcp.tool
async def get_all_words(
    limit: Annotated[
        int,
        Field(ge=1, le=50, description="Number of words to return (1-50)"),
    ] = 20,
) -> list[dict]:
    """
    Get a sample of common dictionary words.
    Returns a list of dicts with 'word' and 'translation' keys.
    Useful for generating quizzes with general vocabulary.
    """
    async with session_factory() as session:
        result = await session.execute(
            select(Word).order_by(func.random()).limit(limit)
        )
        words = list(result.scalars().all())
        return [{"word": w.word, "translation": w.translation} for w in words]


@mcp.tool
async def get_recent_words(
    telegram_id: Annotated[str, Field(description="Telegram user ID (string)")],
    limit: Annotated[
        int,
        Field(ge=1, le=20, description="Number of words to return (1-20)"),
    ] = 10,
) -> list[dict]:
    """
    Get the most recently added words for a user.
    Returns a list of dicts with 'word' and 'translation' keys.
    Returns an empty list if the user is not found or has no words.
    """
    async with session_factory() as session:
        user = await UserQueries.get_user(session=session, telegram_id=telegram_id)
        if user is None:
            return []
        result = await session.execute(
            select(UserWord)
            .where(UserWord.user_id == user.id)
            .order_by(UserWord.created_at.desc())
            .limit(limit)
        )
        words = list(result.scalars().all())
        return [{"word": w.word_user, "translation": w.translation_user} for w in words]


@mcp.tool
async def get_user_stats(
    telegram_id: Annotated[str, Field(description="Telegram user ID (string)")],
) -> dict:
    """Get user stats: count of their custom words and total dictionary size."""
    async with session_factory() as session:
        user = await UserQueries.get_user(session=session, telegram_id=telegram_id)
        if user is None:
            return {"user_words_count": 0, "dictionary_size": 0}

        user_words_count = await session.scalar(
            select(func.count(UserWord.id)).where(UserWord.user_id == user.id)
        )
        dictionary_size = await session.scalar(select(func.count(Word.id)))

        return {
            "user_words_count": user_words_count or 0,
            "dictionary_size": dictionary_size or 0,
        }


@mcp.tool
async def get_weak_words(
    telegram_id: Annotated[str, Field(description="Telegram user ID (string)")],
    limit: Annotated[
        int, Field(ge=1, le=20, description="Number of words to return (1-20)")
    ] = 10,
) -> list[dict]:
    """
    Get words the user struggles with most, sorted by error count descending.
    Returns a list of dicts with 'word', 'translation', and 'error_count' keys.
    Only returns words that have been missed at least once.
    """
    async with session_factory() as session:
        user = await UserQueries.get_user(session=session, telegram_id=telegram_id)
        if user is None:
            return []
        words = await WordQueries.get_weak_words(
            session=session, user=user, limit=limit
        )
        return [
            {
                "word": w["word_user"],
                "translation": w["translation_user"],
                "error_count": w["error_count"],
            }
            for w in words
        ]
