import pytest
from unittest.mock import patch, MagicMock
from sqlalchemy import inspect
from src.db.telegram_models import User, Base
from src.db.db_session import database_exists, create_database, drop_database, init_db


def test_tables_created(engine):
    """
    Проверяет наличие всех таблиц базы данных, определённых в метаданных SQLAlchemy.

    Args:
        engine: SQLAlchemy engine для подключения к тестовой базе данных.

    Asserts:
        Все таблицы из Base.metadata.tables существуют в базе.
    """
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    for table in Base.metadata.tables.keys():
        assert table in tables, f"Таблица {table} не создана"


def test_insert_and_query(db_session):
    """
    Проверяет вставку записи пользователя и успешный запрос по telegram_id.

    Args:
        db_session: активная сессия SQLAlchemy с тестовой БД.

    Asserts:
        Пользователь успешно сохранён и извлечён с правильными данными.
    """
    user = User(telegram_id="123456789", telegram_username="testuser")
    db_session.add(user)
    db_session.commit()

    queried_user = db_session.query(User).filter_by(telegram_id="123456789").first()
    assert queried_user is not None
    assert queried_user.telegram_username == "testuser"


def test_transaction_rollback(db_session):
    """
    Проверяет откат транзакции: после rollback добавленная запись исчезает.

    Args:
        db_session: активная сессия SQLAlchemy с тестовой БД.

    Asserts:
        После flush запись присутствует, после rollback — отсутствует.
    """
    user = User(telegram_id="999999999", telegram_username="tempuser")
    db_session.add(user)
    db_session.flush()

    count_before = db_session.query(User).filter_by(telegram_id="999999999").count()
    assert count_before == 1

    db_session.rollback()

    count_after = db_session.query(User).filter_by(telegram_id="999999999").count()
    assert count_after == 0


def test_database_exists_true():
    """
    Проверяет поведение функции database_exists при наличии базы данных.

    Использует мокирование подключения и возвращает True, когда запрос показывает, что база есть.
    """
    with patch('src.db.db_session.create_engine') as mock_create_engine:
        mock_conn = MagicMock()
        mock_create_engine.return_value.connect.return_value.__enter__.return_value = mock_conn
        mock_conn.execute.return_value.scalar.return_value = 1

        assert database_exists() is True
        mock_conn.execute.assert_called()


def test_database_exists_false():
    """
    Проверяет поведение функции database_exists при отсутствии базы данных.

    Использует мокирование подключения и возвращает False, когда запрос показывает, что базы нет.
    """
    with patch('src.db.db_session.create_engine') as mock_create_engine:
        mock_conn = MagicMock()
        mock_create_engine.return_value.connect.return_value.__enter__.return_value = mock_conn
        mock_conn.execute.return_value.scalar.return_value = None

        assert database_exists() is False
        mock_conn.execute.assert_called()


def test_create_database_calls_execute():
    """
    Проверяет, что функция create_database вызывает execute и логгирование.

    Использует мокирование подключения и логгера.
    """
    with patch('src.db.db_session.create_engine') as mock_create_engine, \
         patch('src.db.db_session.logger') as mock_logger:
        mock_conn = MagicMock()
        mock_create_engine.return_value.connect.return_value.__enter__.return_value = mock_conn

        create_database()
        mock_conn.execute.assert_called()
        mock_logger.info.assert_called()


def test_drop_database_calls_execute():
    """
    Проверяет, что функция drop_database вызывает execute и логгирование.

    Использует мокирование подключения и логгера.
    """
    with patch('src.db.db_session.create_engine') as mock_create_engine, \
         patch('src.db.db_session.logger') as mock_logger:
        mock_conn = MagicMock()
        mock_create_engine.return_value.connect.return_value.__enter__.return_value = mock_conn

        drop_database()
        mock_conn.execute.assert_called()
        mock_logger.info.assert_called()


def test_init_db_calls_create_all():
    """
    Проверяет, что функция init_db вызывает create_all метаданных и логгирует действие.

    Использует мокирование метода create_all и логгера.
    """
    with patch('src.db.db_session.Base.metadata.create_all') as mock_create_all, \
         patch('src.db.db_session.logger') as mock_logger:
        init_db()
        mock_create_all.assert_called()
        mock_logger.info.assert_called()
