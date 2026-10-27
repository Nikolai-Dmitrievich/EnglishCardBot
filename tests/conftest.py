"""
Pytest configuration and fixtures for the English Card Bot.

This module provides an isolated testing environment, including an in-memory
SQLite database, a mocked Telegram API session for capturing bot responses,
and a test client for simulating user interactions.
"""

import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import TelegramMethod
from aiogram.types import Chat, Message, Update
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from src.core.database import DatabaseMiddleware
from src.db.models import Base, Word
from src.db.queries import TECH_WORDS_DATA
from src.handlers import ai_quiz, common, quiz, stat, words

# ==============================================================================
# ENVIRONMENT SETUP
# ==============================================================================
# Provide stub values to prevent tests from failing due to missing .env files
# or attempting to connect to a real database.
_ENV_STUBS = {
    "BOT__TOKEN": "42:TEST-TOKEN",
    "DB__POSTGRES_USER": "postgres",
    "DB__POSTGRES_PASSWORD": "postgres",
    "DB__POSTGRES_HOST": "localhost",
    "DB__POSTGRES_PORT": "5433",
    "DB__POSTGRES_DB": "test_db",
}
for _key, _value in _ENV_STUBS.items():
    os.environ.setdefault(_key, _value)

# ==============================================================================
# CONSTANTS
# ==============================================================================
TEST_TOKEN = "42:TEST-TOKEN"
TEST_USER_ID = 111
TEST_CHAT_ID = 111


# ==============================================================================
# MOCK TELEGRAM API SESSION
# ==============================================================================
class RecordingSession(BaseSession):
    """
    A mock session that captures outgoing messages instead of making network requests.

    This allows verification of response texts and keyboard layouts without
    the need to mock every individual API call.
    """

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[TelegramMethod[Any]] = []
        self._message_id = 0
        # Chat/message of the last sent message: callback queries must be
        # attached to a real message, otherwise handlers that read
        # ``callback.message`` bail out early.
        self.last_chat_id = TEST_CHAT_ID
        self.last_message_id = 0

    async def close(self) -> None:
        pass

    async def make_request(
        self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None
    ) -> Any:
        self.sent.append(method)
        self._message_id += 1

        # Returned messages are mounted on the bot via ``as_``: handlers
        # routinely chain ``.delete()`` / ``.edit_text()`` on the reply, and
        # those helpers need the bot instance to build their request.
        #
        # Edits target an existing message, so its id must be preserved —
        # otherwise a callback built from the response would point at a
        # message that never existed. Checked first because EditMessageText
        # carries ``text`` too.
        if hasattr(method, "chat_id") and hasattr(method, "message_id"):
            result = Message(
                message_id=method.message_id,
                date=datetime.now(tz=UTC),
                chat=Chat(id=method.chat_id, type="private"),  # type: ignore[arg-type]
            ).as_(bot)
            self.last_chat_id = result.chat.id
            self.last_message_id = result.message_id
            return result

        if hasattr(method, "chat_id") and hasattr(method, "text"):
            result = Message(
                message_id=self._message_id,
                date=datetime.now(tz=UTC),
                chat=Chat(id=method.chat_id, type="private"),  # type: ignore[arg-type]
                text=method.text,  # type: ignore[arg-type]
            ).as_(bot)
            self.last_chat_id = result.chat.id
            self.last_message_id = result.message_id
            return result

        return Message(
            message_id=self._message_id,
            date=datetime.now(tz=UTC),
            chat=Chat(id=TEST_CHAT_ID, type="private"),
        ).as_(bot)

    async def stream_content(
        self,
        url: str,
        headers: dict | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes]:
        yield b""

    @property
    def texts(self) -> list[str]:
        """Retrieve the text content of all sent messages.

        Only message-carrying calls count here. ``AnswerCallbackQuery`` also
        exposes a ``text`` field (the toast/alert), which is ``None`` for
        plain acknowledgements and would otherwise surface as the literal
        string ``"None"`` and mask the real reply.
        """
        return [
            str(method.text)  # type: ignore[attr-defined]
            for method in self.sent
            if hasattr(method, "chat_id") and getattr(method, "text", None) is not None
        ]

    def last_keyboard(self) -> list[str]:
        """Return button labels of the last message that had a keyboard."""
        for method in reversed(self.sent):
            markup = getattr(method, "reply_markup", None)
            if markup is None:
                continue
            # ReplyKeyboardMarkup
            if hasattr(markup, "keyboard"):
                return [button.text for row in markup.keyboard for button in row]
            # InlineKeyboardMarkup
            if hasattr(markup, "inline_keyboard"):
                return [button.text for row in markup.inline_keyboard for button in row]
        return []

    def clear(self) -> None:
        """Clear the history of sent messages."""
        self.sent.clear()


# ==============================================================================
# DATABASE FIXTURES
# ==============================================================================
@pytest_asyncio.fixture
async def engine() -> AsyncGenerator[AsyncEngine]:
    """
    Provide an in-memory SQLite engine.

    Uses StaticPool to maintain a single connection, ensuring that the
    in-memory database schema persists across multiple session creations.
    """
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield test_engine
    await test_engine.dispose()


@pytest_asyncio.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Provide an asynchronous session factory bound to the test engine."""
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession]:
    """Provide an active asynchronous database session for individual tests."""
    async with session_factory() as test_session:
        yield test_session


@pytest_asyncio.fixture
async def common_words(session: AsyncSession) -> list[tuple[str, str]]:
    """
    Populate the common dictionary with test data.

    Ensures the quiz logic has a baseline of words to draw from during tests.
    """
    data = [
        ("компьютер", "computer"),
        ("сеть", "network"),
        ("код", "code"),
        ("облако", "cloud"),
    ]
    session.add_all(
        [Word(word=word, translation=translation) for word, translation in data]
    )
    await session.commit()
    return data


# ==============================================================================
# AIOGRAM FIXTURES
# ==============================================================================
@pytest_asyncio.fixture
async def bot() -> AsyncGenerator[Bot]:
    """Provide a Bot instance configured with the RecordingSession."""
    recording_session = RecordingSession()
    test_bot = Bot(token=TEST_TOKEN, session=recording_session)
    yield test_bot
    await test_bot.session.close()


@pytest.fixture
def dp(session_factory: async_sessionmaker[AsyncSession]):
    """
    Provide a configured Dispatcher instance.

    Routers are attached in the same order as in src/main.py to ensure
    StateFilter resolution matches the production environment.

    The routers are module-level singletons, so they are detached on teardown.
    Without this, the second test using the fixture fails with aiogram's
    "Router is already attached" error.
    """
    dispatcher = Dispatcher(storage=MemoryStorage())

    routers = (common.router, stat.router, ai_quiz.router, quiz.router, words.router)
    for router in routers:
        dispatcher.include_router(router)

    middleware = DatabaseMiddleware(session_factory)
    dispatcher.message.middleware(middleware)
    dispatcher.callback_query.middleware(middleware)

    yield dispatcher

    dispatcher.sub_routers.clear()
    for router in routers:
        router._parent_router = None


class TestClient:
    """Drives the dispatcher with synthetic updates and reads bot replies.

    Bound to a specific (bot, dispatcher) pair so the same client can be used
    against the plain router stack and the one that also registers ai_quiz.
    """

    def __init__(self, bot: Bot, dp: Dispatcher) -> None:
        self.bot = bot
        self.dp = dp
        self.update_id = 0

    async def send(self, text: str, user_id: int = TEST_USER_ID) -> None:
        self.update_id += 1
        update = Update.model_validate(
            {
                "update_id": self.update_id,
                "message": {
                    "message_id": self.update_id,
                    "date": int(datetime.now(tz=UTC).timestamp()),
                    "chat": {"id": user_id, "type": "private"},
                    "from": {
                        "id": user_id,
                        "is_bot": False,
                        "first_name": "Nikolai",
                    },
                    "text": text,
                },
            },
            context={"bot": self.bot},
        )
        await self.dp.feed_update(self.bot, update)

    async def tap(self, data: str, user_id: int = TEST_USER_ID) -> None:
        """Simulate an inline button tap on the bot's last sent message.

        Inline keyboards are a separate update type, so tests that need to
        press a button (rather than type the same label as text) have to
        build a CallbackQuery themselves. The message is taken from the
        session's bookkeeping because handlers routinely read
        ``callback.message`` to edit or reply to the tapped message.
        """
        self.update_id += 1
        session = self.bot.session
        update = Update.model_validate(
            {
                "update_id": self.update_id,
                "callback_query": {
                    "id": str(self.update_id),
                    "from": {
                        "id": user_id,
                        "is_bot": False,
                        "first_name": "Nikolai",
                    },
                    "message": {
                        "message_id": session.last_message_id,  # type: ignore[attr-defined]
                        "date": int(datetime.now(tz=UTC).timestamp()),
                        "chat": {"id": user_id, "type": "private"},
                        "from": {
                            "id": 42,
                            "is_bot": True,
                            "first_name": "TestBot",
                        },
                    },
                    "chat_instance": "test-chat-instance",
                    "data": data,
                },
            },
            context={"bot": self.bot},
        )
        await self.dp.feed_update(self.bot, update)

    @property
    def texts(self) -> list[str]:
        return self.bot.session.texts  # type: ignore[attr-defined]

    @property
    def last_text(self) -> str:
        return self.bot.session.texts[-1]  # type: ignore[attr-defined]

    @property
    def keyboard(self) -> list[str]:
        return self.bot.session.last_keyboard()  # type: ignore[attr-defined]

    def clear(self) -> None:
        self.bot.session.clear()  # type: ignore[attr-defined]


@pytest.fixture
def client(bot: Bot, dp: Dispatcher) -> TestClient:
    """Provide a test client wired to the plain (non-AI) router stack."""
    return TestClient(bot, dp)


@pytest.fixture
def client_ai(bot: Bot, dp: Dispatcher) -> TestClient:
    """Test client that also reaches the ai_quiz router.

    Uses the same ``dp`` fixture (which now includes ``ai_quiz.router``) so
    there is no router-conflict between the two clients.
    """
    return TestClient(bot, dp)


@pytest_asyncio.fixture
async def fsm_state(bot: Bot, dp: Dispatcher):
    """
    Provide helpers to read FSM state and data directly from storage.

    ``StorageBase.get_state`` returns the raw state string (or None) in
    aiogram 3.x, so no unwrapping of a state object is required here.
    """

    async def get_state(user_id: int = TEST_USER_ID) -> str | None:
        key = StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id)
        return await dp.storage.get_state(key)

    async def get_data(user_id: int = TEST_USER_ID) -> dict:
        key = StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id)
        return await dp.storage.get_data(key) or {}

    return {"state": get_state, "data": get_data}


@pytest.fixture
def tech_words() -> list[tuple[str, str]]:
    """Provide the default technical words dataset for seeding tests."""
    return TECH_WORDS_DATA
