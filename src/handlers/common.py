"""
Common command handlers for the English Card Bot.

This module provides handlers for basic bot commands such as /start,
/stop, and /help, serving as the entry point for user interactions.
"""

from aiogram import Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.queries import UserQueries

router = Router()


@router.message(Command("start"))
async def start_handler(
    message: types.Message,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """
    Handle the /start command.

    Greets the user, ensures their record exists in the database,
    and immediately launches the first quiz question.

    Args:
        message: The incoming Telegram message.
        session: The active asynchronous SQLAlchemy session.
        state: The FSM context for managing user state.
    """
    user = await UserQueries.get_or_create_user(
        session=session,
        telegram_id=str(message.from_user.id),
        username=message.from_user.first_name,
    )

    greeting_name = user.telegram_username or "друг"
    await message.answer(
        f"Привет, {greeting_name}! Добро пожаловать! Начинаем викторину по английским словам."
    )

    from src.handlers.quiz import send_quiz

    await send_quiz(
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
    brief usage instructions for the bot.

    Args:
        message: The incoming Telegram message.
    """
    help_text = (
        "📚 *Доступные команды:*\n\n"
        "/start - начать викторину по английским словам\n"
        "/stop - остановить викторину и сбросить состояние\n"
        "/help - показать это сообщение с подсказками\n\n"
        "Также используйте кнопки для добавления, удаления слов и перехода к следующему вопросу."
    )
    await message.answer(help_text, parse_mode="Markdown")
