"""
Quiz handlers for the English Card Bot.

This module provides the core quiz logic, including sending random
translation questions, processing user answers, and managing the
quiz flow through the FSM state machine.
"""

import random

from aiogram import Router, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import User
from src.db.queries import WordQueries
from src.states import MyStates

router = Router()


class CommandText:
    """Container for the text labels used on the quiz keyboard buttons."""

    ADD_WORD = "Добавить слово ➕"
    DELETE_WORD = "Удалить слово 🔙"
    NEXT = "Дальше ⏭"
    CANCEL = "Отмена"


async def send_quiz(
    message: types.Message,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    """
    Send a new quiz question to the user.

    Fetches a random word from the user's custom dictionary or the
    common dictionary, generates incorrect translation options, and
    displays them as a reply keyboard. The correct answer and the
    list of options are stored in the FSM state for later validation.

    Args:
        message: The incoming Telegram message used as the response target.
        session: The active asynchronous SQLAlchemy session.
        user: The User instance for whom the quiz is generated.
        state: The FSM context used to persist quiz data between messages.
    """
    word, correct_translation, _ = await WordQueries.get_random_word_for_user(
        session=session,
        user=user,
    )

    # The query returns both values or neither; checking both keeps the
    # narrowed types usable as str below.
    if word is None or correct_translation is None:
        await message.answer("В базе нет слов для викторины. Добавьте новые слова.")
        return

    wrong_translations = await WordQueries.get_wrong_translations(
        session=session,
        user=user,
        correct_translation=correct_translation,
        limit=3,
    )

    options = wrong_translations + [correct_translation]
    random.shuffle(options)

    buttons = [types.KeyboardButton(text=opt) for opt in options]
    buttons.extend(
        [
            types.KeyboardButton(text=CommandText.NEXT),
            types.KeyboardButton(text=CommandText.ADD_WORD),
            types.KeyboardButton(text=CommandText.DELETE_WORD),
        ]
    )
    keyboard = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    markup = types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

    await message.answer(
        f"Выбери правильный перевод слова:\n👉 {word}",
        reply_markup=markup,
    )

    await state.set_state(MyStates.waiting_for_quiz_answer)
    await state.update_data(
        target_word=word,
        correct_translation=correct_translation,
        options=options,
    )


@router.message(StateFilter(MyStates.waiting_for_quiz_answer))
async def quiz_answer_handler(
    message: types.Message,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    """
    Process the user's answer during an active quiz session.

    Routes the incoming message to the appropriate action based on
    the pressed button: advance to the next question, open the
    add-word flow, open the delete-word flow, or validate the
    selected translation against the correct answer stored in FSM.

    Args:
        message: The incoming Telegram message containing the user's answer.
        session: The active asynchronous SQLAlchemy session.
        user: The User instance currently taking the quiz.
        state: The FSM context holding the current quiz data.
    """
    data = await state.get_data()
    text = message.text
    correct_translation = data.get("correct_translation")
    options = data.get("options", [])

    if text == CommandText.NEXT:
        await send_quiz(
            message=message,
            session=session,
            user=user,
            state=state,
        )
        return

    if text == CommandText.ADD_WORD:
        from src.handlers.words import start_add_word

        await start_add_word(message=message, state=state)
        return

    if text == CommandText.DELETE_WORD:
        from src.handlers.words import start_delete_word

        await start_delete_word(
            message=message,
            session=session,
            user=user,
            state=state,
        )
        return

    if text == "/help":
        from src.handlers.common import help_handler

        await help_handler(message=message)
        return

    if text == correct_translation:
        await message.answer(
            f"Отлично! ✅ {data.get('target_word')} -> {correct_translation}"
        )
        await state.clear()
        await send_quiz(
            message=message,
            session=session,
            user=user,
            state=state,
        )
        return

    if text in options:
        buttons = [
            types.KeyboardButton(text=opt if opt != text else f"{opt} ❌")
            for opt in options
        ]
        buttons.extend(
            [
                types.KeyboardButton(text=CommandText.NEXT),
                types.KeyboardButton(text=CommandText.ADD_WORD),
                types.KeyboardButton(text=CommandText.DELETE_WORD),
            ]
        )
        keyboard = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
        markup = types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)
        await message.answer(
            f"Неправильно! Попробуй ещё раз 👉 {data.get('target_word')}",
            reply_markup=markup,
        )
        return

    await message.answer("Пожалуйста, выберите вариант из кнопок.")
