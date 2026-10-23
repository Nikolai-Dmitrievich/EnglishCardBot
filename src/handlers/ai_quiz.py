"""
AI-powered quiz handler using Ollama and MCP tools.

This module provides a /quiz_ai command that generates a personalized
quiz by combining user word data (via MCP tools) with an LLM. The
generated quiz is kept in the FSM so that inline answer buttons can be
validated against the exact questions the user was shown.
"""

import json
import logging
import time

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from fastmcp.client import Client
from fastmcp.client.transports.memory import FastMCPTransport
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.client import LLMUnavailableError, llm_client
from src.ai.prompts import TUTOR_PROMPT_V2
from src.ai.schemas import LLMInvalidOutputError, QuizResponse
from src.ai.utils import extract_tool_data, validate_quiz
from src.core.config import get_settings
from src.db.models import User
from src.db.queries import WordQueries
from src.mcp.tools import mcp
from src.states import MyStates

# Deliberately the same payload as the plain quiz's stop button: both flows
# end the session the same way, and the handlers are kept apart by their
# StateFilters rather than by distinct payloads.
STOP_QUIZ_DATA = "stop_quiz"

logger = logging.getLogger(__name__)
router = Router()

# FSM data key holding the serialized QuizResponse currently on screen.
QUIZ_DATA_KEY = "ai_quiz"

# FSM data key for AI quiz statistics.
STATS_KEY = "ai_quiz_stats"

SHOW_PREFIX = "show:"
# Prefixed with "ai" on purpose: the plain quiz uses the bare "ans:" prefix and
# ai_quiz.router is registered before quiz.router, so a shared prefix would let
# this router swallow plain-quiz callbacks and report them as stale.
ANSWER_PREFIX = "aians:"
NEXT_QUESTION_DATA = "ainext:"
NEW_QUIZ_DATA = "new_quiz"
ADD_WORD_DATA = "add_word"

SHOW_ANSWER_BUTTON = "✅ Показать ответ"
NEXT_QUESTION_BUTTON = "Пропустить ⏭"
STOP_QUIZ_BUTTON = "⏹ Стоп"
NEW_QUIZ_BUTTON = "🔄 Новый квиз"
ADD_WORD_BUTTON = "📝 Добавить слово"

STALE_QUIZ_ALERT = "Этот квиз больше не актуален — нажмите «Новый квиз»."

# Attempts allowed for one /quiz_ai request. The second one only happens when
# the generated quiz passes Pydantic but fails the fill-in-the-blank check,
# i.e. the model leaked the answer into the sentence.
MAX_QUIZ_ATTEMPTS = 2

# Appended on a retry to name the exact rule the model broke, instead of
# resending the same prompt and hoping for a different answer.
RETRY_FIX_NOTE = (
    "\n\nCRITICAL FIX: your previous response broke the fill-in-the-blank rule. "
    "Every question must contain the blank ____ and must NOT contain the correct "
    "option anywhere in its sentence. Rewrite all questions accordingly."
)


async def _send_fallback_quiz(message: Message, reason: str) -> Message:
    return await message.answer(f"{reason}. Вот обычный квиз:")


def _render_single_question(
    quiz: QuizResponse, question_no: int
) -> tuple[str, list[list[InlineKeyboardButton]]]:
    """Build the text and inline keyboard for a single AI quiz question.

    Questions are shown one-by-one. The correct answer is hidden until
    the user taps "Показать ответ" or picks the wrong option.
    """
    total = len(quiz.questions)
    if question_no < 1 or question_no > total:
        return "", []

    q = quiz.questions[question_no - 1]
    text = f"Вопрос {question_no}/{total}\n\n{q.question}"
    keyboard: list[list[InlineKeyboardButton]] = []

    for j, opt in enumerate(q.options):
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=opt, callback_data=f"{ANSWER_PREFIX}{question_no}:{j}"
                )
            ]
        )
    keyboard.append(
        [
            InlineKeyboardButton(
                text=SHOW_ANSWER_BUTTON, callback_data=f"{SHOW_PREFIX}{question_no}"
            )
        ]
    )

    if question_no < total:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=NEXT_QUESTION_BUTTON,
                    callback_data=f"{NEXT_QUESTION_DATA}{question_no}",
                )
            ]
        )

    # Stop button is always shown so the user can end the session at any time.
    keyboard.append(
        [
            InlineKeyboardButton(
                text=STOP_QUIZ_BUTTON,
                callback_data=STOP_QUIZ_DATA,
            )
        ]
    )

    return text, keyboard


async def _send_quiz(
    message: Message,
    quiz: QuizResponse,
    state: FSMContext,
) -> Message:
    """Send the first question of the AI quiz."""
    text, keyboard = _render_single_question(quiz, question_no=1)
    sent = await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )

    await state.set_state(MyStates.waiting_for_ai_quiz_answer)
    await state.update_data(
        {
            QUIZ_DATA_KEY: quiz.model_dump_json(),
            STATS_KEY: {
                "correct": 0,
                "incorrect": 0,
                "answered": [],
                "total": len(quiz.questions),
                "current_question": 1,
            },
        }
    )
    return sent


def _render_results(correct: int, incorrect: int, total: int) -> str:
    """Build the final results screen for a completed AI quiz."""
    answered = correct + incorrect
    accuracy = round((correct / answered) * 100) if answered else 0

    if accuracy >= 90:
        emoji, verdict = "🏆", "Потрясающе! Ты мастер!"
    elif accuracy >= 70:
        emoji, verdict = "👍", "Хороший результат!"
    elif accuracy >= 50:
        emoji, verdict = "🤔", "Неплохо, но можно лучше!"
    else:
        emoji, verdict = "📚", "Нужно ещё потренироваться!"

    return (
        f"{emoji} AI-квиз завершён!\n\n"
        f"Верно: {correct} | Неверно: {incorrect}\n"
        f"Отвечено: {answered} из {total} ({accuracy}%)\n\n"
        f"{verdict}"
    )


def _results_keyboard() -> InlineKeyboardMarkup:
    """Keyboard offered once an AI quiz is finished.

    No «Стоп» here on purpose: this screen is sent right after
    ``state.clear()``, so ``callback_stop_quiz`` (bound to the AI quiz state)
    would never observe the tap and the button would be silently dead.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=NEW_QUIZ_BUTTON, callback_data=NEW_QUIZ_DATA)],
            [InlineKeyboardButton(text=ADD_WORD_BUTTON, callback_data=ADD_WORD_DATA)],
        ]
    )


async def _load_quiz(state: FSMContext) -> tuple[QuizResponse | None, dict | None]:
    """Restore the quiz attached to the current FSM state, if still valid.

    Returns a ``(quiz, stats)`` tuple. Either value may be ``None``.
    """
    raw = await state.get_data()
    quiz_raw = raw.get(QUIZ_DATA_KEY)
    stats_raw = raw.get(STATS_KEY)

    quiz = None
    if quiz_raw:
        try:
            quiz = QuizResponse.model_validate_json(quiz_raw)
        except ValidationError:
            logger.warning("Stored AI quiz failed validation and was dropped")

    stats = stats_raw if stats_raw else None
    return quiz, stats


def _parse_position(data: str | None, prefix: str) -> tuple[int | None, int | None]:
    """Parse ``show:<n>`` / ``ans:<n>:<opt>`` callback payloads.

    Returns ``(None, None)`` for anything malformed so callers can fall
    back to the "quiz is stale" alert instead of raising IndexError.
    """
    if not data or not data.startswith(prefix):
        return None, None

    parts = data.removeprefix(prefix).split(":")
    try:
        question_no = int(parts[0])
    except ValueError:
        return None, None

    option_no = None
    if len(parts) > 1:
        try:
            option_no = int(parts[1])
        except ValueError:
            return None, None

    return question_no, option_no


async def _generate_and_send_quiz(
    message: Message, state: FSMContext
) -> Message | None:
    """Generate a new AI quiz and send it with inline keyboard."""
    if message.from_user is None:
        return await message.answer("Не удалось определить пользователя.")

    thinking = await message.answer("🤖 Генерирую персональный квиз...")

    started = time.perf_counter()
    tools_called_log: list[dict] = []
    content = ""
    max_retries = MAX_QUIZ_ATTEMPTS

    try:
        async with Client(FastMCPTransport(mcp)) as client:
            recent_res = await client.call_tool(
                "get_recent_words",
                {"telegram_id": str(message.from_user.id), "limit": 10},
            )
            tools_called_log.append({"name": "get_recent_words", "status": "ok"})

            weak_res = await client.call_tool(
                "get_weak_words",
                {"telegram_id": str(message.from_user.id), "limit": 5},
            )
            tools_called_log.append({"name": "get_weak_words", "status": "ok"})

            all_res = await client.call_tool(
                "get_all_words",
                {"limit": 20},
            )
            tools_called_log.append({"name": "get_all_words", "status": "ok"})

        user_context: dict = {
            "recent_words": extract_tool_data(recent_res) or [],
            "weak_words": extract_tool_data(weak_res) or [],
            "all_words": extract_tool_data(all_res) or [],
        }

        final_prompt = TUTOR_PROMPT_V2.format(
            user_data=json.dumps(user_context, ensure_ascii=False, indent=2)
        )

        quiz: QuizResponse | None = None
        for attempt in range(max_retries):
            content = await llm_client.complete_with_format(
                messages=[
                    {"role": "user", "content": final_prompt},
                ],
                format_schema=QuizResponse.model_json_schema(),
                timeout=get_settings().ai.request_timeout_seconds,
            )

            parsed = QuizResponse.model_validate_json(content)

            for q in parsed.questions:
                if q.correct_index >= len(q.options):
                    raise LLMInvalidOutputError("correct_index вне диапазона options")

            errors = validate_quiz(parsed)
            if not errors:
                quiz = parsed
                break

            logger.warning(
                "AI quiz failed fill-in-the-blank check (attempt %d/%d): %s",
                attempt + 1,
                max_retries,
                errors,
            )
            final_prompt += RETRY_FIX_NOTE

        # A question that spells out its own answer teaches nothing, so an
        # still-broken retry falls back to the plain quiz rather than showing it.
        if quiz is None:
            raise LLMInvalidOutputError("questions leaked the correct answer")

        await thinking.delete()
        return await _send_quiz(message, quiz, state)

    except LLMUnavailableError as exc:
        logger.warning("LLM unavailable: %s", type(exc).__name__)
        await thinking.delete()
        await state.clear()
        return await _send_fallback_quiz(message, "AI временно недоступен")
    except (LLMInvalidOutputError, ValidationError) as exc:
        logger.error("=" * 50)
        logger.error("LLM VALIDATION ERROR: %s", exc)
        logger.error("RAW LLM OUTPUT:\n%s", content)
        logger.error("=" * 50)
        await thinking.delete()
        await state.clear()
        return await _send_fallback_quiz(message, "Не удалось создать AI-квиз")
    except Exception:
        logger.exception("Unexpected AI error")
        await thinking.delete()
        await state.clear()
        return await _send_fallback_quiz(message, "Произошла ошибка")
    finally:
        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "AI request completed in %dms. Tools: %s",
            latency_ms,
            tools_called_log,
        )


async def start_ai_quiz(message: Message, state: FSMContext) -> Message | None:
    """Launch an AI quiz for a message that carries no command text.

    The /quiz_ai handler rejects payloads longer than 500 chars because that
    guard is about user-typed prompts. A menu tap has no text at all, so it
    enters through here and skips straight to generation.
    """
    if not get_settings().ai.enabled:
        return await _send_fallback_quiz(message, "AI отключен в настройках")

    return await _generate_and_send_quiz(message, state)


@router.message(Command("quiz_ai"))
async def handle_quiz_ai(message: Message, state: FSMContext) -> Message | None:
    if not get_settings().ai.enabled:
        return await _send_fallback_quiz(message, "AI отключен в настройках")

    if message.text is None or len(message.text) > 500:
        if message.text is None:
            return await message.answer(
                "Команда поддерживается только в текстовых сообщениях."
            )
        return await message.answer("Запрос слишком длинный (макс. 500 символов).")

    return await _generate_and_send_quiz(message, state)


@router.callback_query(F.data == NEW_QUIZ_DATA)
async def callback_new_quiz(
    callback: CallbackQuery, state: FSMContext
) -> Message | None:
    message = callback.message
    if not isinstance(message, Message):
        await callback.answer()
        return None

    await callback.answer()
    return await _generate_and_send_quiz(message, state)


@router.callback_query(F.data.startswith(SHOW_PREFIX))
async def callback_show_answer(callback: CallbackQuery, state: FSMContext) -> None:
    """Reveal the correct answer and explanation for the current question."""
    message = callback.message
    if not isinstance(message, Message):
        await callback.answer()
        return

    question_no, _ = _parse_position(callback.data, SHOW_PREFIX)
    quiz, stats = await _load_quiz(state)

    if (
        quiz is None
        or question_no is None
        or not 1 <= question_no <= len(quiz.questions)
    ):
        await callback.answer(STALE_QUIZ_ALERT, show_alert=True)
        return

    question = quiz.questions[question_no - 1]
    await message.answer(
        f"Ответ на вопрос {question_no}: {question.options[question.correct_index]}\n"
        f"{question.explanation_ru}"
    )
    await callback.answer()


@router.callback_query(F.data.startswith(ANSWER_PREFIX))
async def callback_quiz_answer(
    callback: CallbackQuery,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    """Check a tapped option against the quiz stored in the FSM."""
    message = callback.message
    if not isinstance(message, Message):
        await callback.answer()
        return

    question_no, option_no = _parse_position(callback.data, ANSWER_PREFIX)
    quiz, stats = await _load_quiz(state)

    if quiz is None or question_no is None or option_no is None:
        await callback.answer(STALE_QUIZ_ALERT, show_alert=True)
        return
    if not 1 <= question_no <= len(quiz.questions):
        await callback.answer(STALE_QUIZ_ALERT, show_alert=True)
        return

    question = quiz.questions[question_no - 1]
    if not 0 <= option_no < len(question.options):
        await callback.answer()
        return

    # Skip if already answered
    if stats is None:
        await callback.answer(STALE_QUIZ_ALERT, show_alert=True)
        return

    answered: list = stats.get("answered", [])
    if question_no in answered:
        await callback.answer()
        return

    is_correct = option_no == question.correct_index
    correct_count = stats.get("correct", 0) + (1 if is_correct else 0)
    incorrect_count = stats.get("incorrect", 0) + (0 if is_correct else 1)
    new_answered = answered + [question_no]
    total = stats.get("total", len(quiz.questions))

    if is_correct:
        await message.answer(
            f"Верно ✅ {question.options[option_no]}\n{question.explanation_ru}"
        )
    else:
        correct_option = question.options[question.correct_index]
        await message.answer(
            f"Неверно ❌ Вы выбрали: {question.options[option_no]}\n"
            f"Правильный ответ: {correct_option}\n{question.explanation_ru}"
        )
        await WordQueries.record_error_for_translation(
            session=session,
            user=user,
            translation=correct_option,
        )

    await callback.answer("Верно!" if is_correct else "Неверно")

    # Advance from the question that was just answered, not from the previously
    # stored cursor — otherwise the same question is served over and over.
    next_q = question_no + 1

    if len(new_answered) >= total or next_q > total:
        await message.answer(
            _render_results(correct_count, incorrect_count, total),
            reply_markup=_results_keyboard(),
        )
        await state.clear()
        return

    await state.update_data(
        {
            STATS_KEY: {
                "correct": correct_count,
                "incorrect": incorrect_count,
                "answered": new_answered,
                "total": total,
                "current_question": next_q,
            }
        }
    )

    text, keyboard = _render_single_question(quiz, question_no=next_q)
    await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


@router.callback_query(F.data.startswith(NEXT_QUESTION_DATA))
async def callback_next_question(callback: CallbackQuery, state: FSMContext) -> None:
    """Advance to the next question after the current one is answered."""
    message = callback.message
    if not isinstance(message, Message):
        await callback.answer()
        return

    question_no, _ = _parse_position(callback.data, NEXT_QUESTION_DATA)
    quiz, stats = await _load_quiz(state)

    if quiz is None or question_no is None:
        await callback.answer(STALE_QUIZ_ALERT, show_alert=True)
        return
    if stats is None:
        await callback.answer(STALE_QUIZ_ALERT, show_alert=True)
        return

    total = stats.get("total", len(quiz.questions))
    next_q = question_no + 1

    if next_q > total:
        # Reached the end without answering everything — still wrap up.
        await message.answer(
            _render_results(stats.get("correct", 0), stats.get("incorrect", 0), total),
            reply_markup=_results_keyboard(),
        )
        await state.clear()
        await callback.answer()
        return

    # Skipping moves the cursor but leaves the counters untouched.
    await state.update_data(
        {
            STATS_KEY: {
                "correct": stats.get("correct", 0),
                "incorrect": stats.get("incorrect", 0),
                "answered": stats.get("answered", []),
                "total": total,
                "current_question": next_q,
            }
        }
    )

    text, keyboard = _render_single_question(quiz, question_no=next_q)
    await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )
    await callback.answer()


@router.callback_query(
    StateFilter(MyStates.waiting_for_ai_quiz_answer), F.data == STOP_QUIZ_DATA
)
async def callback_stop_quiz(callback: CallbackQuery, state: FSMContext) -> None:
    """End the AI quiz from the inline «⏹ Стоп» button.

    Scoped to the AI quiz state so the plain quiz keeps serving its own stop
    button: both flows share the payload, only the state differs.
    """
    from src.handlers.common import stop_handler

    message = callback.message
    await callback.answer()

    if not isinstance(message, Message):
        await state.clear()
        return

    # Telegram keeps the keyboard of an edited message clickable, and with the
    # state cleared none of its buttons can be served any more.
    await message.edit_reply_markup(reply_markup=None)
    await stop_handler(message=message, state=state)


@router.callback_query(F.data == ADD_WORD_DATA)
async def callback_add_word(callback: CallbackQuery, state: FSMContext) -> None:
    message = callback.message
    if not isinstance(message, Message):
        await callback.answer()
        return

    from src.handlers.words import start_add_word

    await start_add_word(message=message, state=state)
    await callback.answer()


# StateFilter("*"): /add must also work while a quiz or another flow is active,
# otherwise quiz.router's catch-all StateFilter(waiting_for_quiz_answer) wins.
@router.message(StateFilter("*"), Command("add"))
async def handle_add_word(message: Message, state: FSMContext) -> None:
    from src.handlers.words import start_add_word

    await start_add_word(message=message, state=state)
