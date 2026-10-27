"""
Word management handlers for the English Card Bot.

This module provides handlers for adding new custom words to the user's
dictionary and deleting existing ones. It implements a multi-step FSM
flow that validates language-specific input before persisting data.
"""

from aiogram import F, Router, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import User
from src.db.queries import WordQueries
from src.states import MyStates
from src.utils.validators import is_english, is_russian

router = Router()

NON_TEXT_REPLY = "Пожалуйста, отправьте обычное текстовое сообщение."

DELETE_PREFIX = "delete:"


async def start_add_word(message: types.Message, state: FSMContext) -> None:
    """
    Initiate the add-word flow by prompting the user for a Russian word.

    Transitions the FSM into the state awaiting Russian input.

    No reply keyboard is offered: the flow is abandoned with /stop, so a
    persistent «Отмена» button above the input line only got in the way of
    typing.

    Args:
        message: The incoming Telegram message used as the response target.
        state: The FSM context used to persist the add-word flow state.
    """
    await message.answer("Введите новое русское слово для добавления:")
    await state.set_state(MyStates.waiting_for_russian_word)


@router.message(StateFilter(MyStates.waiting_for_russian_word))
async def add_word_get_russian(message: types.Message, state: FSMContext) -> None:
    """
    Validate and store the Russian word entered by the user.

    If the input is invalid, the user is asked to try again. Otherwise,
    the word is stored in FSM data and the state transitions to awaiting
    the English translation.

    Args:
        message: The incoming Telegram message containing the Russian word.
        state: The FSM context used to persist the add-word flow state.
    """
    text = message.text
    if text is None:
        await message.answer(NON_TEXT_REPLY)
        return

    new_word = text.strip()

    if not is_russian(new_word):
        await message.answer(
            "Пожалуйста, введите корректное русское слово (только русские буквы)."
        )
        return

    await state.update_data(new_word=new_word)
    await state.set_state(MyStates.waiting_for_translation_english)
    await message.answer("Введите перевод на английский:")


@router.message(StateFilter(MyStates.waiting_for_translation_english))
async def add_word_get_english(
    message: types.Message,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    """
    Validate the English translation and persist the new word pair.

    If the translation is valid and the Russian word is still available
    in FSM data, the word pair is added to the user's dictionary. The
    FSM state is then cleared and the quiz resumes.

    Args:
        message: The incoming Telegram message containing the translation.
        session: The active asynchronous SQLAlchemy session.
        user: The User instance who owns the new word.
        state: The FSM context holding the previously entered Russian word.
    """
    text = message.text
    if text is None:
        await message.answer(NON_TEXT_REPLY)
        return

    translation = text.strip()

    if not is_english(translation):
        await message.answer(
            "Пожалуйста, введите корректный английский перевод "
            "(только английские буквы)."
        )
        return

    data = await state.get_data()
    new_word = data.get("new_word")

    if not new_word:
        await message.answer(
            "🚫 Ошибка: не удалось получить русское слово. Попробуйте снова."
        )
        await state.clear()
        await message.answer("Напишите /start для продолжения викторины.")
        return

    added = await WordQueries.add_user_word(
        session=session,
        user=user,
        word_user=new_word,
        translation_user=translation,
    )
    await state.clear()

    if added:
        await message.answer(
            f"Слово '{new_word} -> {translation}' добавлено в ваш словарь."
        )
    else:
        await message.answer(f"Слово '{new_word}' уже есть в вашем словаре.")


async def start_delete_word(
    message: types.Message,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    """
    Initiate the delete-word flow by listing the user's custom words.

    Fetches all words belonging to the user and displays them as inline
    buttons. If the user has no words, the flow is terminated immediately
    with a notification.

    Args:
        message: The incoming Telegram message used as the response target.
        session: The active asynchronous SQLAlchemy session.
        user: The User instance whose words should be listed.
        state: The FSM context used to persist the delete-word flow state.
    """
    user_words_objs = await WordQueries.get_user_words(session=session, user=user)
    user_words = [uw.word_user for uw in user_words_objs]

    if not user_words:
        await message.answer("🚫 У вас нет добавленных слов для удаления.")
        return

    keyboard: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=word, callback_data=f"{DELETE_PREFIX}{index}")]
        for index, word in enumerate(user_words)
    ]
    await message.answer(
        "Выберите слово для удаления:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )
    # Callback data is capped at 64 bytes by Telegram, so the buttons carry the
    # word's position rather than the word itself.
    await state.update_data(delete_candidates=user_words)
    await state.set_state(MyStates.waiting_for_word_to_delete)


@router.callback_query(
    StateFilter(MyStates.waiting_for_word_to_delete),
    F.data.startswith(DELETE_PREFIX),
)
async def delete_word_confirm(
    callback: types.CallbackQuery,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    """
    Delete the word behind the tapped button and resume the quiz.

    The index in the payload is resolved against the candidate list captured
    when the flow started, so a stale keyboard cannot delete a word that was
    added afterwards.

    Args:
        callback: The incoming callback query carrying the word's position.
        session: The active asynchronous SQLAlchemy session.
        user: The User instance who owns the word.
        state: The FSM context holding the delete-word flow state.
    """
    message = callback.message
    if not isinstance(message, types.Message):
        await callback.answer()
        return

    data = await state.get_data()
    candidates: list = data.get("delete_candidates", [])

    try:
        index = int((callback.data or "").removeprefix(DELETE_PREFIX))
        word_to_delete = candidates[index]
    except (ValueError, IndexError):
        await callback.answer("Это слово уже недоступно.", show_alert=True)
        return

    deleted = await WordQueries.delete_user_word(
        session=session,
        user=user,
        word_user=word_to_delete,
    )

    await state.clear()
    # Telegram keeps the markup of an edited message clickable, and with the
    # state cleared a second tap would no longer be scoped to this flow.
    await message.edit_reply_markup(reply_markup=None)

    if deleted:
        await message.answer(f"Слово '{word_to_delete}' удалено из вашего словаря.")
    else:
        await message.answer(f"Слово '{word_to_delete}' не найдено в вашем словаре.")

    await callback.answer()


@router.message(StateFilter(MyStates.waiting_for_word_to_delete))
async def delete_word_text_hint(message: types.Message) -> None:
    """Nudge the user back to the keyboard when they type instead of tapping.

    The word list is inline-only now, so free text has no meaning here; without
    a reply the bot would simply look broken. /stop is handled by common.router,
    which is registered earlier, so it never reaches this handler.
    """
    await message.answer("Выберите слово кнопкой выше или отправьте /stop.")
