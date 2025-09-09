import logging
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from .model_db import NAME_DB, LOGIN, PASSWORD, DB_HOST, DB_PORT
from .telegram_models import Base


logger = logging.getLogger(__name__)

DATABASE_URL = f"postgresql://{LOGIN}:{PASSWORD}@{DB_HOST}:{DB_PORT}/{NAME_DB}?client_encoding=utf8"
SYSTEM_DATABASE_URL = f"postgresql://{LOGIN}:{PASSWORD}@{DB_HOST}:{DB_PORT}/postgres"


engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)

def get_session():
    return Session()


def database_exists():
    """
    Проверяет, существует ли база данных с именем NAME_DB.

    Returns:
        bool: True, если база существует, иначе False
    """
    system_engine = create_engine(SYSTEM_DATABASE_URL)
    try:
        with system_engine.connect() as conn:
            query = text("SELECT 1 FROM pg_database WHERE datname = :name")
            result = conn.execute(query, {"name": NAME_DB})
            exists = result.scalar() is not None
            logger.info(f"Database '{NAME_DB}' existence check: {exists}")
            return exists
    except OperationalError as e:
        logger.error(f"Ошибка подключения к системной базе: {e}")
        return False


def create_database():
    """
    Создает базу данных с именем NAME_DB.
    """
    system_engine = create_engine(SYSTEM_DATABASE_URL)
    try:
        with system_engine.connect() as conn:
            conn.execution_options(isolation_level="AUTOCOMMIT")
            conn.execute(text(f'CREATE DATABASE "{NAME_DB}" ENCODING \'UTF8\''))
        logger.info(f"Database '{NAME_DB}' успешно создана.")
    except Exception as e:
        logger.error(f"Ошибка при создании базы данных: {e}")


def drop_database():
    """
    Удаляет базу данных с именем NAME_DB.
    """
    system_engine = create_engine(SYSTEM_DATABASE_URL)
    try:
        with system_engine.connect() as conn:
            conn.execution_options(isolation_level="AUTOCOMMIT")
            conn.execute(text(f"""
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = '{NAME_DB}' AND pid <> pg_backend_pid();
            """))
            conn.execute(text(f'DROP DATABASE IF EXISTS "{NAME_DB}"'))
        logger.info(f"Database '{NAME_DB}' успешно удалена.")
    except Exception as e:
        logger.error(f"Ошибка при удалении базы данных: {e}")


def init_db():
    """
    Инициализирует структуру базы данных, создавая все таблицы, определенные в моделях SQLAlchemy.
    """
    logger.info("Инициализация таблиц в базе...")
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    logger.info(f"Таблицы созданы: {tables}")

    from .translator_db import add_word, tech_words_data

    add_word(tech_words_data)
    logger.info("Общий словарь слов заполнен.")