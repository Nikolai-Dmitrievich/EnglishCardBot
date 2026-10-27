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
# Marks buttons on an already-answered question as inert: tapping them again
# must not be counted against the *next* question's options.
DISABLED_PREFIX = "noop:"
# Inline counterpart of the /stop command: tapping it must end the session
# without the user having to type the command.
STOP_QUIZ_DATA = "stop_quiz"

# Hard cap of ``AnswerCallbackQuery.text`` enforced by the Telegram Bot API.
MAX_ALERT_TEXT_LENGTH = 200


async def _pick_question_word(
    session: AsyncSession,
    user: User,
    exclude_words: list[str],
) -> tuple[str | None, str | None]:
    """Return one random (word, translation) pair, excluding already-asked words."""
    result = await WordQueries.get_random_word_for_user(
        session=session,
        user=user,
        exclude_words=exclude_words,
    )
    return result[0], result[1]


class CommandText:
    """Container for the text labels used on the quiz keyboard buttons."""

    NEXT = "Дальше ⏭"
    STOP = "⏹ Стоп"


async def _build_quiz_keyboard(
    options: list[str],
    correct_translation: str,
    answered: set[int],
    correct_index: int,
    lock_options: bool = False,
) -> InlineKeyboardMarkup:
    """Build an inline keyboard for a quiz question.

    Args:
        options: List of answer options.
        correct_translation: The correct answer text.
        answered: Set of indices already answered (shown with ✅/❌).
        correct_index: Index of the correct answer.
        lock_options: If True, every option becomes inert. Telegram keeps the
            keyboard of an edited message clickable, so once a question is
            answered all its options must stop pointing at ``ans:<idx>`` —
            otherwise a tap on the old message would be evaluated against the
            options of the question that is currently active.
    """
    keyboard: list[list[InlineKeyboardButton]] = []

    for idx, opt in enumerate(options):
        # Only tapped options get marked, and the mark has to reflect whether
        # the pick was right: marking every answered index with ❌ made a
        # correct choice look like a mistake. The correct option is never
        # highlighted before it is actually chosen, otherwise the keyboard
        # would give the answer away.
        if idx in answered:
            text = f"{opt} ✅" if idx == correct_index else f"{opt} ❌"
        else:
            text = opt

        prefix = DISABLED_PREFIX if lock_options else ANSWER_PREFIX
        keyboard.append(
            [InlineKeyboardButton(text=text, callback_data=f"{prefix}{idx}")]
        )

    # Navigation row: skip the current question or end the session entirely.
    # Word management lives in the /start and /help menus now — keeping it on
    # every question crowded the keyboard for no benefit.
    keyboard.append(
        [
            InlineKeyboardButton(
                text="Пропустить ⏭",
                callback_data=f"{NEXT_PREFIX}skip",
            ),
            InlineKeyboardButton(
                text=CommandText.STOP,
                callback_data=STOP_QUIZ_DATA,
            ),
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
    # Get current quiz progress. question_number is owned exclusively by this
    # function: answer handlers only update the counters, so a question is
    # never counted twice.
    data = await state.get_data()
    question_number = data.get("question_number", 0) + 1
    correct_count = data.get("correct_count", 0)
    incorrect_count = data.get("incorrect_count", 0)

    # Words already asked in this session. Repeating a word the user just saw
    # teaches nothing, so the pool shrinks question by question; once it runs
    # dry it is reset, which is the only way to reach QUIZ_LENGTH with a small
    # dictionary.
    used_words: list[str] = list(data.get("used_words", []))
    previous_word = data.get("target_word")
    if previous_word is not None:
        used_words.append(previous_word)

    word, correct_translation = await _pick_question_word(
        session=session,
        user=user,
        exclude_words=used_words,
    )

    if word is None or correct_translation is None:
        # Every known word was already asked this session — start a new cycle.
        used_words = []
        word, correct_translation = await _pick_question_word(
            session=session,
            user=user,
            exclude_words=[],
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
        used_words=used_words,
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


@router.callback_query(StateFilter(MyStates.waiting_for_quiz_answer))
async def quiz_callback_handler(
    callback: CallbackQuery,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    """
    Process inline button callbacks during an active quiz session.

    Handles answer selection, skipping questions and stopping the session.
    Word management lives in the /start and /help menu instead: keeping it off
    the question keyboard leaves room for the answers themselves.

    Args:
        callback: The incoming callback query.
        session: The active asynchronous SQLAlchemy session.
        user: The User instance currently taking the quiz.
        state: The FSM context holding the current quiz data.
    """
    data = await state.get_data()
    callback_data = callback.data or ""
    message = callback.message

    # Stop callback — the inline twin of /stop, so both paths must produce
    # the very same reply.
    if callback_data == STOP_QUIZ_DATA:
        from src.handlers.common import stop_handler

        await callback.answer()
        # The keyboard of an edited message stays clickable, and once the
        # state is cleared none of these buttons can be served any more.
        if isinstance(message, types.Message):
            await message.edit_reply_markup(reply_markup=None)
            await stop_handler(message=message, state=state)
        else:
            await state.clear()
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
        # The alert is the only place a miss is explained: the keyboard merely
        # marks options ✅/❌, so naming the right one here is what teaches.
        # Trimmed to Telegram's 200-char limit for callback answers.
        await callback.answer(
            f"Неверно ❌ Правильный ответ: {options[correct_index]}"[
                :MAX_ALERT_TEXT_LENGTH
            ],
            show_alert=True,
        )

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
