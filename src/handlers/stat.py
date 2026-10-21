"""
Statistics handler for the English Card Bot.

Provides a /stat command with period selection buttons (day, week, month,
custom range) and displays error statistics for the selected period.
"""

from datetime import UTC, datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import User
from src.db.queries import WordQueries
from src.states import MyStates

router = Router()

CALLBACK_PREFIX = "stat:"
MAX_WORDS_IN_REPORT = 10

PERIOD_TODAY = "day"
PERIOD_WEEK = "week"
PERIOD_MONTH = "month"
PERIOD_CUSTOM = "custom"
PERIOD_REFRESH = "refresh"

PROMPT_TEXT = "📊 Выберите период для статистики:"
DATE_PROMPT_TEXT = (
    "📅 Отправьте начальную дату в формате ДД.ММ.ГГГГ "
    "(например: 15.10.2026) или отправьте /stop, чтобы отменить."
)
NO_ERRORS_TEXT = "🎉 За этот период ошибок не было!"


def _build_period_keyboard() -> InlineKeyboardMarkup:
    """Build keyboard with period selection buttons."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📅 Сегодня", callback_data=f"{CALLBACK_PREFIX}{PERIOD_TODAY}"
                ),
                InlineKeyboardButton(
                    text="📆 Неделя", callback_data=f"{CALLBACK_PREFIX}{PERIOD_WEEK}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🗓 Месяц", callback_data=f"{CALLBACK_PREFIX}{PERIOD_MONTH}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔢 Свой период",
                    callback_data=f"{CALLBACK_PREFIX}{PERIOD_CUSTOM}",
                ),
            ],
        ]
    )


def _get_period_range(period: str) -> tuple[str, datetime, datetime]:
    """Return (label, start, end) for a predefined period.

    Boundaries are UTC because ``word_errors.error_date`` is stored with a
    timezone and Postgres compares instants, not wall-clock values.
    """
    now = datetime.now(tz=UTC)

    if period == PERIOD_TODAY:
        return "сегодня", now.replace(hour=0, minute=0, second=0, microsecond=0), now

    if period == PERIOD_WEEK:
        start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return "текущую неделю", start, now

    if period == PERIOD_MONTH:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return "текущий месяц", start, now

    raise ValueError(f"Unknown period: {period}")


def _format_period_stats(
    stats: dict,
    period_label: str,
) -> str:
    """Render the statistics report for the selected period."""
    lines = [
        f"📊 Статистика ошибок за {period_label}",
        f"Всего ошибок: {stats['total_errors']}",
    ]

    daily_errors: dict = stats["daily_errors"]
    if daily_errors:
        lines.append("\n📅 Ошибки по дням:")
        lines.extend(f"  {day}: {count}" for day, count in sorted(daily_errors.items()))

    by_word: list[dict] = stats["by_word"]
    if by_word:
        lines.append("\n🔤 Слова с ошибками:")
        for item in by_word[:MAX_WORDS_IN_REPORT]:
            lines.append(f"  {item['word']} — {item['translation']}: {item['errors']}")

    return "\n".join(lines)


async def _send_report(
    message: Message,
    session: AsyncSession,
    user: User,
    period_label: str,
    start_date: datetime,
    end_date: datetime,
) -> None:
    """Fetch statistics for the range and answer with a formatted report."""
    stats = await WordQueries.get_errors_by_period(
        session=session,
        user=user,
        start_date=start_date,
        end_date=end_date,
    )

    if stats["total_errors"] == 0:
        await message.answer(NO_ERRORS_TEXT)
        return

    await message.answer(_format_period_stats(stats, period_label))


@router.message(Command("stat"))
async def stat_handler(message: Message) -> None:
    """Handle /stat command — show period selection keyboard."""
    await message.answer(PROMPT_TEXT, reply_markup=_build_period_keyboard())


@router.callback_query(F.data.startswith(CALLBACK_PREFIX))
async def stat_callback(
    callback: CallbackQuery,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    """Handle period selection and display statistics."""
    period = (callback.data or "").removeprefix(CALLBACK_PREFIX)
    message = callback.message

    if not isinstance(message, Message):
        await callback.answer()
        return

    if period == PERIOD_CUSTOM:
        await state.set_state(MyStates.waiting_for_stat_period_start)
        await message.edit_text(DATE_PROMPT_TEXT)
        await callback.answer()
        return

    if period == PERIOD_REFRESH:
        await state.clear()
        await message.edit_text(PROMPT_TEXT, reply_markup=_build_period_keyboard())
        await callback.answer()
        return

    try:
        period_label, start_date, end_date = _get_period_range(period)
    except ValueError:
        await callback.answer("Неизвестный период.", show_alert=True)
        return

    await callback.answer()
    await _send_report(
        message=message,
        session=session,
        user=user,
        period_label=period_label,
        start_date=start_date,
        end_date=end_date,
    )


@router.message(StateFilter(MyStates.waiting_for_stat_period_start))
async def stat_custom_period_handler(
    message: Message,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    """Finish the custom-period flow started by the «Свой период» button.

    The state is cleared on both success and parse failure: a malformed date
    must not leave the user stuck in a state that swallows further messages.
    """
    text = message.text
    if text is None:
        await message.answer("Пожалуйста, отправьте дату текстом.")
        return

    try:
        start_date = datetime.strptime(text.strip(), "%d.%m.%Y").replace(tzinfo=UTC)
    except ValueError:
        await state.clear()
        await message.answer(
            "Неверный формат даты. Используйте ДД.ММ.ГГГГ и нажмите /stat."
        )
        return

    await state.clear()

    if start_date > datetime.now(tz=UTC):
        await message.answer("Начальная дата не может быть в будущем.")
        return

    await _send_report(
        message=message,
        session=session,
        user=user,
        period_label=f"период с {start_date.strftime('%d.%m.%Y')}",
        start_date=start_date,
        end_date=datetime.now(tz=UTC),
    )
