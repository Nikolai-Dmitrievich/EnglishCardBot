import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.bot import DefaultBotProperties
from src.db.telegram_models import Base


@pytest.fixture(scope="session")
def engine():
    """
    Создает и возвращает SQLAlchemy engine для in-memory SQLite базы данных.
    Выполняет создание всех таблиц из метаданных перед тестами и удаляет.

    Returns:
        Engine: SQLAlchemy engine, подключенный к временной БД SQLite.
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)


@pytest.fixture
def db_session(engine):
    """
    Создает SQLAlchemy сессию с транзакцией и вложенным savepoint для изоляции тестов.

    Поддерживает откат изменений после каждого теста для чистоты состояния базы.

    Args:
        engine: SQLAlchemy engine для базы данных.

    Yields:
        Session: Активная SQLAlchemy сессия.
    """
    connection = engine.connect()
    transaction = connection.begin()
    Session = scoped_session(sessionmaker(bind=connection))
    session = Session()

    nested = connection.begin_nested()

    @sa.event.listens_for(session, "after_transaction_end")
    def restart_savepoint(session, transaction):
        nonlocal nested
        if not nested.is_active:
            nested = connection.begin_nested()

    yield session

    session.close()
    if transaction.is_active:
        transaction.rollback()
    connection.close()
    Session.remove()


@pytest_asyncio.fixture
async def bot():
    """
    Асинхронная фикстура, создающая экземпляр Aiogram Bot для тестов.

    BOT_TOKEN установлен для тестовой среды, сессия aiohttp используется для HTTP запросов.

    Yields:
        Bot: Экземпляр бота Aiogram.
    """
    BOT_TOKEN = "1234567890:AA_BCD-EFGHIJKLMNOPQRSTUVWXYZ_testtoken"
    session = AiohttpSession()
    default_props = DefaultBotProperties(parse_mode="HTML")
    bot_instance = Bot(token=BOT_TOKEN, session=session, default=default_props)
    yield bot_instance
    await bot_instance.session.close()


@pytest_asyncio.fixture
async def dp(bot):
    """
    Асинхронная фикстура, создающая Dispatcher для Aiogram с in-memory хранилищем состояния.

    Args:
        bot: Экземпляр бота Aiogram.

    Yields:
        Dispatcher: Конфигурированный диспетчер для обработки апдейтов.
    """
    storage = MemoryStorage()
    dispatcher = Dispatcher(bot=bot, storage=storage)
    yield dispatcher
