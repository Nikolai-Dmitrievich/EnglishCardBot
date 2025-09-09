import pytest
from unittest.mock import AsyncMock
from datetime import datetime, timezone
from aiogram.types import Message, User, Chat, Update
from aiogram.filters import CommandStart
from src.db.translator_db import get_or_create_user, add_user_word, get_random_word_for_user


@pytest.mark.asyncio
async def test_full_bot_flow(bot, dp, db_session, mocker):
    """
    Интеграционный тест полного потока работы бота.

    Проверяет, что при получении команды /start:
    - создается или получается пользователь из базы,
    - добавляется слово в персональный словарь пользователя,
    - выбирается случайное слово для викторины,
    - отправляется ответ пользователю с приветствием и словом.

    Параметры:
        bot: объект бота Aiogram.
        dp: диспетчер Aiogram с зарегистрированными хендлерами.
        db_session: сессия базы данных для тестирования.
        mocker: фикстура pytest-mock для создания моков.

    Асинхронный тест, использующий мокирование отправки сообщений,
    создание тестовых апдейтов и проверку корректности вызова ответов.
    """
    @dp.message(CommandStart())
    async def cmd_start(message: Message):
        user = get_or_create_user(db_session, str(message.from_user.id), message.from_user.first_name)
        add_user_word(db_session, user.telegram_id, "компьютер", "computer")
        word, translation, _ = get_random_word_for_user(db_session, user.telegram_id, user.telegram_username)
        await message.answer(f"Привет, {user.telegram_username}! Слово: {word} ({translation})")


    mock_answer = mocker.patch("aiogram.types.message.Message.answer", new_callable=AsyncMock)


    user = User(id=12345, is_bot=False, first_name="TestUser")
    chat = Chat(id=12345, type="private")


    start_msg = Message(
        message_id=1,
        from_user=user,
        chat=chat,
        date=datetime.now(timezone.utc),
        text="/start",
    )
    update = Update(update_id=1, message=start_msg)


    await dp.feed_update(bot, update)


    assert mock_answer.called
    response_text = mock_answer.call_args[0][0]
    assert "Привет" in response_text
    assert "компьютер" in response_text
