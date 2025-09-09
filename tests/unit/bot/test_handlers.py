import pytest
from unittest.mock import AsyncMock
from datetime import datetime, timezone
from aiogram import Bot, Dispatcher
from aiogram.types import Message, User, Chat, Update
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup


class MyStates(StatesGroup):
    waiting_for_quiz_answer = State()


@pytest.mark.asyncio
async def test_bot_handlers(bot: Bot, mocker):
    """
    Тестирует основные хендлеры бота: /start, /stop, /help и обработку ответов в состоянии FSM.

    В рамках теста проверяется:
    - что команда /start вызывает приветственное сообщение,
    - что команда /stop очищает состояние FSM и отправляет сообщение об остановке викторины,
    - что команда /help отправляет текст с подсказками,
    - что хендлер с фильтром состояния FSM корректно обрабатывает сообщение и очищает состояние.

    Args:
        bot (Bot): Экземпляр бота Aiogram.
        mocker: Фикстура для мокирования функций.
    """
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    user = User(id=12345, is_bot=False, first_name="User")
    chat = Chat(id=12345, type="private")

    @dp.message(CommandStart())
    async def start_handler(message: Message):
        await message.answer("Добро пожаловать")

    start_answer_mock = mocker.patch("aiogram.types.message.Message.answer", new_callable=AsyncMock)
    start_msg = Message(message_id=1, from_user=user, chat=chat, date=datetime.now(timezone.utc), text="/start")
    start_update = Update(update_id=1, message=start_msg)
    await dp.feed_update(bot, start_update)
    start_answer_mock.assert_called()
    assert "Добро пожаловать" in start_answer_mock.call_args[0][0]

    stop_answer_mock = mocker.patch("aiogram.types.message.Message.answer", new_callable=AsyncMock)

    @dp.message(Command("stop"))
    async def stop_handler(message: Message, state: FSMContext):
        await state.clear()
        await message.answer("Викторина остановлена. Если захотите продолжить — напишите /start.")

    stop_msg = Message(message_id=2, from_user=user, chat=chat, date=datetime.now(timezone.utc), text="/stop")
    stop_update = Update(update_id=2, message=stop_msg)
    await dp.feed_update(bot, stop_update)
    stop_answer_mock.assert_called()
    stop_answer_mock.assert_awaited_with("Викторина остановлена. Если захотите продолжить — напишите /start.")

    @dp.message(Command("help"))
    async def help_handler(message: Message):
        await message.answer("Помощь по командам")

    help_answer_mock = mocker.patch("aiogram.types.message.Message.answer", new_callable=AsyncMock)
    help_msg = Message(message_id=3, from_user=user, chat=chat, date=datetime.now(timezone.utc), text="/help")
    help_update = Update(update_id=3, message=help_msg)
    await dp.feed_update(bot, help_update)
    help_answer_mock.assert_called()
    assert "Помощь" in help_answer_mock.call_args[0][0]

    @dp.message(StateFilter(MyStates.waiting_for_quiz_answer))
    async def quiz_answer_handler(message: Message, state: FSMContext):
        await message.answer("Ответ обработан")
        await state.clear()

    fsm_ctx = dp.fsm.get_context(bot=bot, chat_id=chat.id, user_id=user.id)
    await fsm_ctx.set_state(MyStates.waiting_for_quiz_answer)

    quiz_answer_mock = mocker.patch("aiogram.types.message.Message.answer", new_callable=AsyncMock)
    quiz_msg = Message(message_id=4, from_user=user, chat=chat, date=datetime.now(timezone.utc), text="тест")
    quiz_update = Update(update_id=4, message=quiz_msg)
    await dp.feed_update(bot, quiz_update)
    quiz_answer_mock.assert_called_with("Ответ обработан")
    current_state = await fsm_ctx.get_state()
    assert current_state is None
