import sqlalchemy as db
from sqlalchemy import func
from sqlalchemy.orm import Session
from .telegram_models import User, Word, UserWord
from .db_session import get_session

session = get_session()


def get_or_create_user(session, telegram_id: str, username: str):
    """
    Получает пользователя по telegram_id или создает нового.

    Args:
        session (Session): SQLAlchemy сессия.
        telegram_id (str): Telegram ID пользователя.
        username (str): Telegram username пользователя.

    Returns:
        User: Существующий или новый объект пользователя.
    """
    user = session.query(User).filter_by(telegram_id=telegram_id).first()
    if user:
        return user
    user = User(telegram_id=telegram_id, telegram_username=username)
    session.add(user)
    session.commit()
    return user


def get_user_words(session, telegram_id):
    """
    Возвращает список пользовательских слов по telegram_id пользователя.

    Args:
        session (Session): SQLAlchemy сессия.
        telegram_id (str): Telegram ID пользователя.

    Returns:
        list[UserWord]: Список пользовательских слов.
    """
    return session.query(UserWord).filter(
        UserWord.telegram_id == telegram_id
    ).all()


def get_random_word_for_user(session, telegram_id_str, username):
    """
    Возвращает случайное слово (и его перевод) для пользователя.
    Сначала ищет среди пользовательских слов, затем среди общего словаря.

    Args:
        session (Session): SQLAlchemy сессия.
        telegram_id_str (str): Telegram ID пользователя.
        username (str): Telegram username пользователя.

    Returns:
        tuple: (слово, перевод, флаг является ли слово пользовательским)
    """
    user = get_or_create_user(session, telegram_id_str, username)

    user_words_q = session.query(
        UserWord.word_user.label('word'),
        UserWord.translation_user.label('translation'),
        db.literal_column("TRUE").label('is_user_word')
    ).filter(UserWord.telegram_id == telegram_id_str)

    common_words_q = session.query(
        Word.word.label('word'),
        Word.translation.label('translation'),
        db.literal_column("FALSE").label('is_user_word')
    )

    combined_q = user_words_q.union_all(common_words_q)

    with session.no_autoflush:
        random_word = session.query(combined_q.subquery()).order_by(func.random()).first()

    if random_word:
        return random_word.word, random_word.translation, random_word.is_user_word
    return None, None, False


def add_user_word(session, telegram_id, word_user, translation_user):
    """
    Добавляет новое пользовательское слово, если оно еще не существует.

    Args:
        session (Session): SQLAlchemy сессия.
        telegram_id (str): Telegram ID пользователя.
        word_user (str): Слово пользователя.
        translation_user (str): Перевод слова пользователя.

    Returns:
        bool: True если слово добавлено, False если уже существует.
    """
    existing = session.query(UserWord).filter_by(
        telegram_id=telegram_id,
        word_user=word_user
    ).first()
    if existing:
        return False
    new_word = UserWord(
        telegram_id=telegram_id,
        word_user=word_user,
        translation_user=translation_user
    )
    session.add(new_word)
    session.commit()
    return True


def get_wrong_translations(session, correct_translation, telegram_id_str, username, limit=3):
    """
    Возвращает список неправильных переводов (без правильного ответа).

    Args:
        session (Session): SQLAlchemy сессия.
        correct_translation (str): Правильный перевод, который нужно исключить.
        telegram_id_str (str): Telegram ID пользователя.
        username (str): Telegram username пользователя.
        limit (int): Максимальное число неверных вариантов перевода.

    Returns:
        list[str]: Список неправильных переводов.
    """
    user = get_or_create_user(session, telegram_id_str, username)

    user_translations_q = session.query(UserWord.translation_user).filter(
        UserWord.telegram_id == telegram_id_str,
        UserWord.translation_user != correct_translation
    )

    common_translations_q = session.query(Word.translation).filter(
        Word.translation != correct_translation
    )

    combined_q = user_translations_q.union(common_translations_q)

    with session.no_autoflush:
        wrong_translations = session.query(combined_q.subquery()).order_by(
            func.random()
        ).limit(limit).all()

    return [wt[0] for wt in wrong_translations]


def delete_user_word(session, telegram_id, word_user):
    """
    Удаляет пользовательское слово.

    Args:
        session (Session): SQLAlchemy сессия.
        telegram_id (str): Telegram ID пользователя.
        word_user (str): Слово пользователя для удаления.

    Returns:
        bool: True если слово удалено, False если слово не найдено.
    """
    word_obj = session.query(UserWord).filter_by(
        telegram_id=telegram_id,
        word_user=word_user
    ).first()
    if word_obj:
        session.delete(word_obj)
        session.commit()
        return True
    return False


def add_word(tech_words_data):
    """
    Заполняет общий словарь словами из tech_words_data.

    Args:
        tech_words_data (list of tuples): Список кортежей (слово, перевод).
    """
    try:
        existing_words = {w.word for w in session.query(Word).all()}
        for word, translation in tech_words_data:
            if word not in existing_words:
                session.add(Word(word=word, translation=translation))
        session.commit()
    finally:
        session.close()


# Пример набора слов для общего словаря
tech_words_data = [
    ('компьютер', 'computer'),
    ('программное обеспечение', 'software'),
    ('аппаратное обеспечение', 'hardware'),
    ('сеть', 'network'),
    ('база данных', 'database'),
    ('сервер', 'server'),
    ('клиент', 'client'),
    ('алгоритм', 'algorithm'),
    ('отлаживать', 'debug'),
    ('код', 'code'),
    ('интерфейс', 'interface'),
    ('протокол', 'protocol'),
    ('шифрование', 'encryption'),
    ('межсетевой экран', 'firewall'),
    ('облако', 'cloud'),
    ('виртуализация', 'virtualization'),
    ('компилятор', 'compiler'),
    ('фреймворк', 'framework'),
    ('репозиторий', 'repository'),
]
