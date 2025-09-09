import asyncio
import random
import re
import os
from dotenv import load_dotenv
from src.db.translator_db import (
    get_random_word_for_user,
    get_wrong_translations,
    delete_user_word,
    get_session,
    get_user_words,
    get_or_create_user,
    add_user_word,
)
from aiogram import Bot, Dispatcher, types, Router
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command, StateFilter
from contextlib import asynccontextmanager


load_dotenv()


TOKEN = os.getenv("BOT_TOKEN")


bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()


@asynccontextmanager
async def get_async_session():
    """
    Асинхронный контекстный менеджер для работы с сессией базы данных.

    При входе создает сессию, при выходе коммитит транзакцию.
    В случае ошибки откатывает изменения и закрывает сессию.

    Yields:
        session: SQLAlchemy сессия.
    """
    session = get_session()
    try:
        yield session
        session.commit()
    except:
        session.rollback()
        raise
    finally:
        session.close()


class CommandText:
    ADD_WORD = 'Добавить слово ➕'
    DELETE_WORD = 'Удалить слово 🔙'
    NEXT = 'Дальше ⏭'
    CANCEL = 'Отмена'


class MyStates(StatesGroup):
    """
    Состояния конечного автомата для взаимодействия с пользователем.
    """
    waiting_for_russian_word = State()
    waiting_for_translation_english = State()
    waiting_for_word_to_delete = State()
    waiting_for_quiz_answer = State()


def is_russian(text: str) -> bool:
    """
    Проверяет, содержит ли текст только русские буквы, пробелы или дефисы.

    Args:
        text (str): Исходный текст.

    Returns:
        bool: True, если текст на русском, иначе False.
    """
    return bool(re.fullmatch(r'[а-яА-ЯёЁ\s-]+', text.strip()))


def is_english(text: str) -> bool:
    """
    Проверяет, содержит ли текст только английские буквы, пробелы или дефисы.

    Args:
        text (str): Исходный текст.

    Returns:
        bool: True, если текст на английском, иначе False.
    """
    return bool(re.fullmatch(r'[a-zA-Z\s-]+', text.strip()))


@router.message(Command("start"))
async def start_handler(message: types.Message, state: FSMContext):
    """
    Обработчик команды /start.
    Создает пользователя (если новый), приветствует, показывает помощь и отправляет первый вопрос викторины.

    Args:
        message (types.Message): Входящее сообщение.
        state (FSMContext): Контекст состояния.
    """
    telegram_id_str = str(message.from_user.id)
    first_name = message.from_user.first_name or ""
    async with get_async_session() as session:
        get_or_create_user(session, telegram_id_str, first_name)
    greeting_name = first_name if first_name else "друг"
    await message.answer(f"Привет, {greeting_name}! Добро пожаловать! Начинаем викторину по английским словам.")
    await help_handler(message)
    await send_quiz(message)


@router.message(Command("stop"))
async def stop_handler(message: types.Message, state: FSMContext):
    """
    Обработчик команды /stop.
    Очищает состояние пользователя и сообщает об окончании викторины.

    Args:
        message (types.Message): Входящее сообщение.
        state (FSMContext): Контекст состояния.
    """
    await state.clear()
    await message.answer("Викторина остановлена. Если захотите продолжить — напишите /start.")


@router.message(Command("help"))
async def help_handler(message: types.Message):
    """
    Обработчик команды /help.
    Отправляет описание доступных команд и подсказки.

    Args:
        message (types.Message): Входящее сообщение.
    """
    help_text = (
        "📚 *Доступные команды:*\n\n"
        "/start - начать викторину по английским словам\n"
        "/stop - остановить викторину и сбросить состояние\n"
        "/help - показать это сообщение с подсказками\n\n"
        "Также используйте кнопки для добавления, удаления слов и перехода к следующему вопросу."
    )
    await message.answer(help_text, parse_mode='Markdown')


async def send_quiz(message: types.Message):
    """
    Отправляет пользователю вопрос викторины с выбором правильного перевода.

    Args:
        message (types.Message): Входящее сообщение.
    """
    telegram_id_str = str(message.from_user.id)
    first_name = message.from_user.first_name or ""

    async with get_async_session() as session:
        word, correct_translation, _ = get_random_word_for_user(session, telegram_id_str, first_name)
        if not word:
            await message.answer("В базе нет слов для викторины. Добавьте новые слова.")
            return
        wrong_translations = get_wrong_translations(session, correct_translation, telegram_id_str, first_name, limit=3)

    options = wrong_translations + [correct_translation]
    random.shuffle(options)

    buttons = [types.KeyboardButton(text=opt) for opt in options]
    buttons.extend([
        types.KeyboardButton(text=CommandText.NEXT),
        types.KeyboardButton(text=CommandText.ADD_WORD),
        types.KeyboardButton(text=CommandText.DELETE_WORD),
    ])
    keyboard = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    markup = types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

    await message.answer(f"Выбери правильный перевод слова:\n👉 {word}", reply_markup=markup)

    state = dp.fsm.get_context(bot=message.bot, chat_id=message.chat.id, user_id=message.from_user.id)
    await state.set_state(MyStates.waiting_for_quiz_answer)
    await state.update_data(target_word=word, correct_translation=correct_translation, options=options)


@router.message(StateFilter(MyStates.waiting_for_quiz_answer))
async def quiz_answer_handler(message: types.Message, state: FSMContext):
    """
    Обрабатывает ответы пользователя во время викторины.

    Проверяет текст сообщения и выполняет действия: переход к следующему вопросу,
    начало добавления/удаления слова, проверка правильности ответа.

    Args:
        message (types.Message): Входящее сообщение.
        state (FSMContext): Контекст состояния.
    """
    data = await state.get_data()
    text = message.text
    correct_translation = data.get('correct_translation')
    options = data.get('options', [])

    if text == CommandText.NEXT:
        await send_quiz(message)
        return

    if text == CommandText.ADD_WORD:
        buttons = [types.KeyboardButton(text=CommandText.CANCEL)]
        markup = types.ReplyKeyboardMarkup(keyboard=[buttons], resize_keyboard=True, one_time_keyboard=True)
        await message.answer("Введите новое русское слово для добавления:", reply_markup=markup)
        await state.set_state(MyStates.waiting_for_russian_word)
        return

    if text == CommandText.DELETE_WORD:
        await delete_word_start(message)
        return

    if text == '/help':
        await help_handler(message)
        return

    if text == correct_translation:
        await message.answer(f"Отлично! ✅ {data.get('target_word')} -> {correct_translation}")
        await state.clear()
        await send_quiz(message)
    elif text in options:
        buttons = [types.KeyboardButton(text=opt if opt != text else f"{opt} ❌") for opt in options]
        buttons.extend([
            types.KeyboardButton(text=CommandText.NEXT),
            types.KeyboardButton(text=CommandText.ADD_WORD),
            types.KeyboardButton(text=CommandText.DELETE_WORD),
        ])
        keyboard = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
        markup = types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)
        await message.answer(f"Неправильно! Попробуй ещё раз 👉 {data.get('target_word')}", reply_markup=markup)
    else:
        await message.answer("Пожалуйста, выберите вариант из кнопок.")


@router.message(StateFilter(MyStates.waiting_for_russian_word))
async def add_word_get_russian(message: types.Message, state: FSMContext):
    """
    Обрабатывает ввод нового русского слова для добавления в словарь.

    Проверяет корректность слова и переводит пользователя к вводу английского перевода.

    Args:
        message (types.Message): Входящее сообщение.
        state (FSMContext): Контекст состояния.
    """
    new_word = message.text.strip()
    if new_word == CommandText.CANCEL:
        await message.answer("Добавление слова отменено.")
        await state.clear()
        await send_quiz(message)
        return
    if not is_russian(new_word):
        await message.answer("Пожалуйста, введите корректное русское слово (только русские буквы).")
        return
    await state.update_data(new_word=new_word)
    await state.set_state(MyStates.waiting_for_translation_english)
    buttons = [types.KeyboardButton(text=CommandText.CANCEL)]
    markup = types.ReplyKeyboardMarkup(keyboard=[buttons], resize_keyboard=True, one_time_keyboard=True)
    await message.answer("Введите перевод на английский:", reply_markup=markup)


@router.message(StateFilter(MyStates.waiting_for_translation_english))
async def add_word_get_english(message: types.Message, state: FSMContext):
    """
    Обрабатывает ввод английского перевода для нового слова.

    Добавляет слово в пользовательский словарь и запускает викторину.

    Args:
        message (types.Message): Входящее сообщение.
        state (FSMContext): Контекст состояния.
    """
    translation = message.text.strip()
    if translation == CommandText.CANCEL:
        await message.answer("Добавление слова отменено.")
        await state.clear()
        await send_quiz(message)
        return
    if not is_english(translation):
        await message.answer("Пожалуйста, введите корректный английский перевод (только английские буквы).")
        return
    data = await state.get_data()
    new_word = data.get('new_word')
    if not new_word:
        await message.answer("🚫Ошибка: не удалось получить русское слово. Попробуйте снова.")
        await state.clear()
        await send_quiz(message)
        return
    async with get_async_session() as session:
        added = add_user_word(session, str(message.from_user.id), new_word, translation)
    await state.clear()
    if added:
        await message.answer(f"Слово '{new_word} -> {translation}' добавлено в ваш словарь.")
    else:
        await message.answer(f"Слово '{new_word}' уже есть в вашем словаре.")
    await send_quiz(message)


async def delete_word_start(message: types.Message):
    """
    Запускает процесс удаления слова из пользовательского словаря.

    Предлагает пользователю выбрать слово из списка для удаления.

    Args:
        message (types.Message): Входящее сообщение.
    """
    telegram_id_str = str(message.from_user.id)
    async with get_async_session() as session:
        user_words_objs = get_user_words(session, telegram_id_str)
        user_words = [uw.word_user for uw in user_words_objs]
    if not user_words:
        await message.answer("🚫У вас нет добавленных слов для удаления.")
        return
    buttons = [types.KeyboardButton(text=word) for word in user_words]
    buttons.append(types.KeyboardButton(text=CommandText.CANCEL))
    keyboard = [buttons[i:i+1] for i in range(0, len(buttons), 1)]
    markup = types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, one_time_keyboard=True)
    await message.answer("Выберите слово для удаления:", reply_markup=markup)

    state = dp.fsm.get_context(bot=message.bot, chat_id=message.chat.id, user_id=message.from_user.id)
    await state.set_state(MyStates.waiting_for_word_to_delete)


@router.message(StateFilter(MyStates.waiting_for_word_to_delete))
async def delete_word_confirm(message: types.Message, state: FSMContext):
    """
    Подтверждает удаление слова из пользовательского словаря.

    Удаляет указанное слово или сообщает о неудаче, затем запускает викторину.

    Args:
        message (types.Message): Входящее сообщение.
        state (FSMContext): Контекст состояния.
    """
    word_to_delete = message.text.strip()
    if word_to_delete == CommandText.CANCEL:
        await message.answer("Удаление отменено.")
        await state.clear()
        await send_quiz(message)
        return
    async with get_async_session() as session:
        deleted = delete_user_word(session, str(message.from_user.id), word_to_delete)
    if deleted:
        await message.answer(f"Слово '{word_to_delete}' удалено из вашего словаря.")
    else:
        await message.answer(f"Слово '{word_to_delete}' не найдено в вашем словаре.")
    await state.clear()
    await send_quiz(message)


dp.include_router(router)


if __name__ == "__main__":
    """
    Точка входа для запуска бота.
    Запускает цикл опроса и взаимодействия с Telegram.
    """
    print("Бот запущен")
    asyncio.run(dp.start_polling(bot))
