import os
from dotenv import load_dotenv

load_dotenv()

NAME_DB = os.getenv("DB_NAME")
PASSWORD = os.getenv("DB_PASSWORD")
LOGIN = os.getenv("DB_USER")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")

def get_database_url():
    """
    Формирует URL подключения к базе данных PostgreSQL на основе переменных окружения.

    Переменные окружения загружаются из файла `.env` с помощью python-dotenv.

    Использует параметры:
        - DB_USER: имя пользователя базы данных
        - DB_PASSWORD: пароль пользователя
        - DB_HOST: адрес хоста базы данных
        - DB_PORT: порт подключения
        - DB_NAME: имя базы данных

    Возвращает строку подключения в формате:
    postgresql://<user>:<password>@<host>:<port>/<database>?client_encoding=utf8
    """
    return f"postgresql://{LOGIN}:{PASSWORD}@{DB_HOST}:{DB_PORT}/{NAME_DB}?client_encoding=utf8"
