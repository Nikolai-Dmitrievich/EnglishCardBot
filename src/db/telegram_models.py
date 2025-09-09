import sqlalchemy as db
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime

Base = declarative_base()


class User(Base):
    """
    Модель пользователя Telegram.

    Атрибуты:
        telegram_id (str): Telegram ID уникальный идентификатор пользователя.
        telegram_username (str): Username пользователя в Telegram (может быть пустым).
        registration_date (datetime): Дата и время регистрации пользователя.
        words (relationship): Связь с пользовательскими словами (UserWord).
    """
    __tablename__ = 'users'

    telegram_id = db.Column(db.String(length=100), primary_key=True)
    telegram_username = db.Column(db.String(length=100), nullable=True)
    registration_date = db.Column(
        db.DateTime,
        default=datetime.now
    )

    words = relationship('UserWord', back_populates='user')


class Word(Base):
    """
    Модель слова из общего словаря.

    Атрибуты:
        id (int): Внутренний уникальный идентификатор слова.
        word (str): Слово на русском языке (уникально).
        translation (str): Перевод слова на английский.
    """
    __tablename__ = 'words'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    word = db.Column(db.String(length=200), nullable=False, unique=True)
    translation = db.Column(db.String(length=200), nullable=False)


class UserWord(Base):
    """
    Модель пользовательского слова.

    Атрибуты:
        id (int): Внутренний уникальный идентификатор записи.
        telegram_id (str): Внешний ключ на пользователя.
        word_user (str): Слово, добавленное пользователем (на русском).
        translation_user (str): Перевод слова, добавленный пользователем.
        created_date (datetime): Дата и время добавления слова.
        user (relationship): Связь с пользователем (User).
    Индексы и ограничения:
        - Уникальность пары (user_id, word_user)
        - Индекс по (user_id, word_user)
    """
    __tablename__ = 'users_words'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    telegram_id = db.Column(db.String(length=100), db.ForeignKey('users.telegram_id'), nullable=False)
    word_user = db.Column(db.String(length=200), nullable=False)
    translation_user = db.Column(db.String(length=200), nullable=False)
    created_date = db.Column(
        db.DateTime,
        default=datetime.now
    )

    user = relationship('User', back_populates='words')

    __table_args__ = (
        db.UniqueConstraint('telegram_id', 'word_user', name='uq_user_word'),
        db.Index('idx_user_word', 'telegram_id', 'word_user'),
    )
