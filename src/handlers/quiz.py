"""
Quiz handlers for the English Card Bot.

This module provides the core quiz logic, including sending random
translation questions, processing user answers, and managing the
quiz flow through the FSM state machine.
"""

import random

from aiogram import F, Router, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import User
from src.db.queries import WordQueries
from src.states import MyStates

router = Router()

# Number of questions per quiz session
QUIZ_LENGTH = 10

# Callback data prefixes
ANSWER_PREFIX = "ans:"
NEXT_PREFIX = "next:"
RESULTS_PREFIX = "results:"
# Marks buttons on an already-answered question as inert: tapping them again
# must not be counted against the *next* question's options.
DISABLED_PREFIX = "noop:"
ADD_WORD_DATA = "add_word"
DELETE_WORD_DATA = "delete_word"


class CommandText:
    """Container for the text labels used on the quiz keyboard buttons."""

    ADD_WORD = "Добавить слово ➕"
    DELETE_WORD = "Удалить слово 🔙"
    NEXT = "Дальше ⏭"
    CANCEL = "Отмена"


async def _build_quiz_keyboard(
    options: list[str],
    correct_translation: str,
    answered: set[int],
    correct_index: int,
    is_complete: bool = False,
    lock_options: bool = False,
) -> InlineKeyboardMarkup:
    """Build an inline keyboard for a quiz question.

    Args:
        options: List of answer options.
        correct_translation: The correct answer text.
        answered: Set of indices already answered (shown with ✅/❌).
        correct_index: Index of the correct answer.
        is_complete: If True, show correct/incorrect markers for all options.
        lock_options: If True, every option becomes inert. Telegram keeps the
            keyboard of an edited message clickable, so once a question is
            answered all its options must stop pointing at ``ans:<idx>`` —
            otherwise a tap on the old message would be evaluated against the
            options of the question that is currently active.
    """
    keyboard: list[list[InlineKeyboardButton]] = []

    for idx, opt in enumerate(options):
        if is_complete:
            if idx == correct_index:
                text = f"{opt} ✅"
            elif idx in answered:
                text = f"{opt} ❌"
            else:
                text = opt
        elif idx in answered:
            text = f"{opt} ❌"
        else:
            text = opt

        prefix = DISABLED_PREFIX if lock_options else ANSWER_PREFIX
        keyboard.append(
            [InlineKeyboardButton(text=text, callback_data=f"{prefix}{idx}")]
        )

    # Navigation row
    nav_buttons = [
        InlineKeyboardButton(
            text="📊 Результаты",
            callback_data=f"{RESULTS_PREFIX}show",
        ),
    ]
    if not is_complete:
        nav_buttons.append(
            InlineKeyboardButton(
                text="Пропустить ⏭",
                callback_data=f"{NEXT_PREFIX}skip",
            )
        )
    keyboard.append(nav_buttons)

    # Bottom row: add/delete word
    keyboard.append(
        [
            InlineKeyboardButton(text="➕ Слово", callback_data=ADD_WORD_DATA),
            InlineKeyboardButton(text="🗑 Слово", callback_data=DELETE_WORD_DATA),
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


async def send_quiz(
    message: types.Message,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    """
    Send a new quiz question to the user.

    Fetches a random word, generates incorrect options, and displays
    them as inline buttons. Tracks quiz progress (current question
    number, correct/incorrect counts) in the FSM state.

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

    # Find the index of the correct answer after shuffling
    correct_index = options.index(correct_translation)

    # Get current quiz progress. question_number is owned exclusively by this
    # function: answer handlers only update the counters, so a question is
    # never counted twice.
    data = await state.get_data()
    question_number = data.get("question_number", 0) + 1
    correct_count = data.get("correct_count", 0)
    incorrect_count = data.get("incorrect_count", 0)

    keyboard = await _build_quiz_keyboard(
        options=options,
        correct_translation=correct_translation,
        answered=set(),
        correct_index=correct_index,
    )

    progress_text = f"Вопрос {question_number}/{QUIZ_LENGTH}"
    if correct_count or incorrect_count:
        progress_text += f" | ✅{correct_count} ❌{incorrect_count}"

    await message.answer(
        f"{progress_text}\n\nВыбери правильный перевод слова:\n👉 {word}",
        reply_markup=keyboard,
    )

    await state.set_state(MyStates.waiting_for_quiz_answer)
    await state.update_data(
        target_word=word,
        correct_translation=correct_translation,
        options=options,
        correct_index=correct_index,
        question_number=question_number,
        correct_count=correct_count,
        incorrect_count=incorrect_count,
        answered_indices=[],
    )


@router.callback_query(StateFilter(None), F.data == f"{NEXT_PREFIX}restart")
async def quiz_restart_handler(
    callback: CallbackQuery,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    """Restart the quiz from the final-results screen.

    The results screen is sent right after ``state.clear()``, so the FSM
    is empty by the time its buttons are tapped. ``quiz_callback_handler``
    is bound to ``waiting_for_quiz_answer`` and never sees these taps —
    without this handler the «Начать заново» button would be silently dead.

    ``StateFilter(None)`` keeps the handler scoped to that screen only:
    while a quiz or a word-management flow owns the state, its own
    handlers stay in charge.
    """
    message = callback.message
    if not isinstance(message, types.Message):
        await callback.answer()
        return

    await callback.answer()
    await send_quiz(
        message=message,
        session=session,
        user=user,
        state=state,
    )


@router.callback_query(StateFilter(None), F.data == DELETE_WORD_DATA)
async def results_delete_word_handler(
    callback: CallbackQuery,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    """Handle «🗑 Слово» tapped on the results screen.

    Same reason as ``quiz_restart_handler``: the results-screen keyboard is
    reachable only with an empty state, so word management needs its own
    stateless entry point there.
    """
    from src.handlers.words import start_delete_word

    message = callback.message
    if not isinstance(message, types.Message):
        await callback.answer()
        return

    await start_delete_word(
        message=message,
        session=session,
        user=user,
        state=state,
    )
    await callback.answer()


@router.callback_query(StateFilter(MyStates.waiting_for_quiz_answer))
async def quiz_callback_handler(
    callback: CallbackQuery,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    """
    Process inline button callbacks during an active quiz session.

    Handles answer selection, skipping questions, showing results,
    and navigating to word management.

    Args:
        callback: The incoming callback query.
        session: The active asynchronous SQLAlchemy session.
        user: The User instance currently taking the quiz.
        state: The FSM context holding the current quiz data.
    """
    data = await state.get_data()
    callback_data = callback.data or ""
    message = callback.message

    # Word management callbacks
    if callback_data == ADD_WORD_DATA:
        from src.handlers.words import start_add_word

        if isinstance(message, types.Message):
            await start_add_word(message=message, state=state)
        await callback.answer()
        return

    if callback_data == DELETE_WORD_DATA:
        from src.handlers.words import start_delete_word

        if isinstance(message, types.Message):
            await start_delete_word(
                message=message,
                session=session,
                user=user,
                state=state,
            )
        await callback.answer()
        return

    # Results callback
    if callback_data.startswith(RESULTS_PREFIX):
        correct_count = data.get("correct_count", 0)
        incorrect_count = data.get("incorrect_count", 0)
        total = correct_count + incorrect_count

        if total == 0:
            await callback.answer("Вы ещё не ответили ни на один вопрос.")
            return

        accuracy = round((correct_count / total) * 100) if total > 0 else 0

        if accuracy >= 90:
            emoji = "🏆"
        elif accuracy >= 70:
            emoji = "👍"
        elif accuracy >= 50:
            emoji = "🤔"
        else:
            emoji = "📚"

        await callback.answer(
            f"{emoji} {correct_count}/{total} верно",
            show_alert=True,
        )
        return

    # Next/skip callback. «restart» never reaches this handler: the results
    # screen is sent after state.clear(), so quiz_restart_handler owns it.
    if callback_data.startswith(NEXT_PREFIX):
        await callback.answer()
        if isinstance(message, types.Message):
            await send_quiz(
                message=message,
                session=session,
                user=user,
                state=state,
            )
        return

    # Answer callback
    if callback_data.startswith(ANSWER_PREFIX):
        await _handle_answer(callback, session, user, state, data)
        return

    # Buttons of an already answered question are rewritten to this prefix:
    # acknowledge the tap without touching the current question's counters.
    if callback_data.startswith(DISABLED_PREFIX):
        await callback.answer("На этот вопрос уже отвечен 🙂")
        return

    await callback.answer()


async def _handle_answer(
    callback: CallbackQuery,
    session: AsyncSession,
    user: User,
    state: FSMContext,
    data: dict,
) -> None:
    """Process a single answer selection."""
    callback_data = callback.data or ""
    if not callback_data.startswith(ANSWER_PREFIX):
        return

    try:
        selected_index = int(callback_data.removeprefix(ANSWER_PREFIX))
    except ValueError:
        await callback.answer()
        return

    options = data.get("options", [])
    correct_index = data.get("correct_index")
    target_word = data.get("target_word", "")
    answered_indices: list = data.get("answered_indices", [])
    correct_count = data.get("correct_count", 0)
    incorrect_count = data.get("incorrect_count", 0)
    question_number = data.get("question_number", 0)

    if correct_index is None or selected_index >= len(options):
        await callback.answer()
        return

    # Mark this option as answered to prevent re-answering
    if selected_index in answered_indices:
        await callback.answer()
        return

    new_answered = answered_indices + [selected_index]

    if selected_index == correct_index:
        correct_count += 1
        await callback.answer("Верно! ✅")
    else:
        incorrect_count += 1
        await WordQueries.record_error_for_word(
            session=session,
            user=user,
            word=target_word,
        )
        await callback.answer("Неверно ❌", show_alert=True)

    # question_number is the number of the question currently on screen, so
    # the quiz ends once the last one is answered. send_quiz owns the counter.
    if question_number >= QUIZ_LENGTH:
        # Show final results
        total = correct_count + incorrect_count
        accuracy = round((correct_count / total) * 100) if total > 0 else 0

        if accuracy >= 90:
            emoji = "🏆"
            message = "Потрясающе! Ты мастер!"
        elif accuracy >= 70:
            emoji = "👍"
            message = "Хороший результат!"
        elif accuracy >= 50:
            emoji = "🤔"
            message = "Неплохо, но можно лучше!"
        else:
            emoji = "📚"
            message = "Нужно ещё потренироваться!"

        result_text = (
            f"{emoji} Квиз завершён!\n\n"
            f"Результат: {correct_count}/{QUIZ_LENGTH} ({accuracy}%)\n"
            f"{message}\n\n"
            f"Нажмите «Начать заново», чтобы пройти ещё раз."
        )

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔄 Начать заново",
                        callback_data=f"{NEXT_PREFIX}restart",
                    )
                ],
                [
                    InlineKeyboardButton(text="➕ Слово", callback_data=ADD_WORD_DATA),
                    InlineKeyboardButton(
                        text="🗑 Слово", callback_data=DELETE_WORD_DATA
                    ),
                ],
            ]
        )

        msg = callback.message
        if isinstance(msg, types.Message):
            await msg.answer(result_text, reply_markup=keyboard)
        await state.clear()
        return

    # Persist the counters before send_quiz reads them back.
    await state.update_data(
        correct_count=correct_count,
        incorrect_count=incorrect_count,
        answered_indices=[],
    )

    # Lock the keyboard on the current message so old taps don't count.
    correct_translation = data.get("correct_translation", "")
    keyboard = await _build_quiz_keyboard(
        options=options,
        correct_translation=correct_translation,
        answered=set(new_answered),
        correct_index=correct_index,
        lock_options=True,
    )

    msg = callback.message
    if isinstance(msg, types.Message):
        await msg.edit_reply_markup(reply_markup=keyboard)

    # Send next question (send_quiz owns question_number).
    if isinstance(msg, types.Message):
        await send_quiz(
            message=msg,
            session=session,
            user=user,
            state=state,
        )


@router.message(StateFilter(MyStates.waiting_for_quiz_answer))
async def quiz_answer_handler(
    message: types.Message,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    """
    Process text-based fallback during quiz (e.g. if user types instead of clicking).

    Routes to word management or validates the selected translation.

    Args:
        message: The incoming Telegram message containing the user's answer.
        session: The active asynchronous SQLAlchemy session.
        user: The User instance currently taking the quiz.
        state: The FSM context holding the current quiz data.
    """
    data = await state.get_data()
    text = message.text
    if text is None:
        return

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
        await WordQueries.record_error_for_word(
            session=session,
            user=user,
            word=data.get("target_word", ""),
        )
        await message.answer("Неверно ❌")

        # Rebuild inline keyboard with the wrong option marked
        answered_indices: list = data.get("answered_indices", [])
        correct_index = data.get("correct_index")
        correct_translation = data.get("correct_translation", "")
        new_answered = answered_indices + [options.index(text)]

        if correct_index is None:
            await message.answer(
                f"Неправильно! Попробуй ещё раз 👉 {data.get('target_word')}"
            )
            return

        keyboard = await _build_quiz_keyboard(
            options=options,
            correct_translation=correct_translation,
            answered=set(new_answered),
            correct_index=correct_index,
            lock_options=True,
        )
        await message.answer(
            f"Неправильно! Попробуй ещё раз 👉 {data.get('target_word')}",
            reply_markup=keyboard,
        )

        # Persist counters; send_quiz owns question_number.
        await state.update_data(
            correct_count=data.get("correct_count", 0),
            incorrect_count=data.get("incorrect_count", 0) + 1,
            answered_indices=[],
        )
        return

    await message.answer("Пожалуйста, выберите вариант из кнопок.")
