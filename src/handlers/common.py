"""
Common command handlers for the English Card Bot.

This module provides handlers for basic bot commands such as /start,
/stop, and /help, serving as the entry point for user interactions.
"""

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import User

router = Router()

# Menu payloads are namespaced on purpose. common.router is registered before
# the quiz routers, so a bare "add_word"/"stop_quiz" here would be answered by
# this router even while a quiz owns the screen and would bypass the state
# checks those keyboards rely on.
MENU_PREFIX = "menu:"
START_QUIZ_DATA = f"{MENU_PREFIX}quiz"
START_AI_QUIZ_DATA = f"{MENU_PREFIX}ai_quiz"
STAT_DATA = f"{MENU_PREFIX}stat"
ADD_WORD_DATA = f"{MENU_PREFIX}add_word"
DELETE_WORD_DATA = f"{MENU_PREFIX}delete_word"

START_QUIZ_BUTTON = "➡️ Начать викторину"
START_AI_QUIZ_BUTTON = "🤖 AI-викторина"
STAT_BUTTON = "📊 Статистика"
ADD_WORD_BUTTON = "➕ Добавить слово"
DELETE_WORD_BUTTON = "🗑 Удалить слово"

HELP_TEXT = (
    "📚 <b>Доступные команды:</b>\n\n"
    "<code>/start</code> - показать главное меню с действиями\n"
    "<code>/quiz</code> - начать викторину по английским словам\n"
    "<code>/quiz_ai</code> - персонализированный AI-квиз на основе ваших слов\n"
    "<code>/stat</code> - статистика ошибок за выбранный период\n"
    "<code>/stop</code> - остановить викторину и сбросить состояние\n"
    "<code>/help</code> - показать это сообщение с подсказками\n\n"
    "Кнопки ниже запускают викторину, AI-квиз, статистику и управление "
    "словарём. Во время викторины используйте «Пропустить» и «⏹ Стоп»."
)


def _menu_keyboard() -> InlineKeyboardMarkup:
    """Keyboard with the actions available outside a running quiz.

    Shared by /start and /help: both are entry points users reach from a
    stopped or stuck state, so they must offer exactly the same set of moves.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=START_QUIZ_BUTTON, callback_data=START_QUIZ_DATA
                )
            ],
            [
                InlineKeyboardButton(
                    text=START_AI_QUIZ_BUTTON, callback_data=START_AI_QUIZ_DATA
                ),
                InlineKeyboardButton(text=STAT_BUTTON, callback_data=STAT_DATA),
            ],
            [
                InlineKeyboardButton(text=ADD_WORD_BUTTON, callback_data=ADD_WORD_DATA),
                InlineKeyboardButton(
                    text=DELETE_WORD_BUTTON, callback_data=DELETE_WORD_DATA
                ),
            ],
        ]
    )


@router.message(Command("start"))
async def start_handler(
    message: types.Message,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    """
    Handle the /start command.

    Greets the user, shows the command reference, and offers the action menu.
    The quiz is deliberately not launched here: /start is also how a user
    recovers from a stuck flow, and an immediate question buried the help text
    that explains what the bot can do.

    Args:
        message: The incoming Telegram message.
        session: The active asynchronous SQLAlchemy session.
        user: The User injected by ``DatabaseMiddleware``.
        state: The FSM context for managing user state.
    """
    # Anonymous/channel-posted updates carry no sender, so the middleware
    # could not resolve a dictionary owner for them.
    if message.from_user is None:
        return

    # A fresh /start always returns to the landing screen, so any half-finished
    # word-adding or quiz flow is dropped together with its state.
    await state.clear()

    greeting_name = user.telegram_username or "друг"
    await message.answer(
        f"Привет, {greeting_name}! Добро пожаловать! "
        "Я помогу выучить английские слова через викторину."
    )
    await message.answer(HELP_TEXT, parse_mode="HTML")
    await message.answer("Выберите действие:", reply_markup=_menu_keyboard())


@router.message(Command("quiz"))
async def quiz_handler(
    message: types.Message,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    """
    Handle the /quiz command.

    Starts the plain quiz straight away. /start only opens the menu, so this
    is the shortcut for users who already know they want a round.

    Args:
        message: The incoming Telegram message.
        session: The active asynchronous SQLAlchemy session.
        user: The User injected by ``DatabaseMiddleware``.
        state: The FSM context for managing user state.
    """
    # A new round must not inherit the counters of a half-finished one.
    await state.clear()

    from src.handlers.quiz import send_quiz

    await send_quiz(
        message=message,
        session=session,
        user=user,
        state=state,
    )


@router.callback_query(F.data.startswith(MENU_PREFIX))
async def menu_callback(
    callback: types.CallbackQuery,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    """Route a menu tap to the action it stands for.

    One handler for the whole menu: every branch is a three-line dispatch to a
    flow that already owns its own state machine, so splitting them into
    separate handlers would only repeat the message guards.
    """
    message = callback.message
    data = callback.data or ""

    if not isinstance(message, types.Message):
        await callback.answer()
        return

    await callback.answer()

    if data == START_QUIZ_DATA:
        from src.handlers.quiz import send_quiz

        await send_quiz(
            message=message,
            session=session,
            user=user,
            state=state,
        )
        return

    if data == START_AI_QUIZ_DATA:
        from src.handlers.ai_quiz import start_ai_quiz

        await start_ai_quiz(message=message, state=state)
        return

    if data == STAT_DATA:
        from src.handlers.stat import stat_handler

        await stat_handler(message=message)
        return

    if data == ADD_WORD_DATA:
        from src.handlers.words import start_add_word

        await start_add_word(message=message, state=state)
        return

    if data == DELETE_WORD_DATA:
        from src.handlers.words import start_delete_word

        await start_delete_word(
            message=message,
            session=session,
            user=user,
            state=state,
        )


@router.message(Command("stop"))
async def stop_handler(message: types.Message, state: FSMContext) -> None:
    """
    Handle the /stop command.

    Clears the user's FSM state and notifies them that the quiz
    session has been terminated.

    Args:
        message: The incoming Telegram message.
        state: The FSM context for managing user state.
    """
    await state.clear()
    await message.answer(
        "Викторина остановлена. Если захотите продолжить — напишите /start."
    )


@router.message(Command("help"))
async def help_handler(message: types.Message) -> None:
    """
    Handle the /help command.

    Sends a formatted message listing all available commands and
    brief usage instructions for the bot, followed by the action menu.

    Args:
        message: The incoming Telegram message.
    """
    await message.answer(HELP_TEXT, parse_mode="HTML")
    await message.answer("Выберите действие:", reply_markup=_menu_keyboard())
