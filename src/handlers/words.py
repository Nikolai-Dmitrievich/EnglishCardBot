"""
Word management handlers for the English Card Bot.

This module provides handlers for adding new custom words to the user's
dictionary and deleting existing ones. It implements a multi-step FSM
flow that validates language-specific input before persisting data.
"""

from aiogram import Router, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import User
from src.db.queries import WordQueries
from src.handlers.quiz import CommandText, send_quiz
from src.states import MyStates
from src.utils.validators import is_english, is_russian

router = Router()


async def start_add_word(message: types.Message, state: FSMContext) -> None:
    """
    Initiate the add-word flow by prompting the user for a Russian word.

    Displays a one-time reply keyboard with a cancel button and
    transitions the FSM into the state awaiting Russian input.

    Args:
        message: The incoming Telegram message used as the response target.
        state: The FSM context used to persist the add-word flow state.
    """
    buttons = [types.KeyboardButton(text=CommandText.CANCEL)]
    markup = types.ReplyKeyboardMarkup(
        keyboard=[buttons],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.answer(
        "Введите новое русское слово для добавления:",
        reply_markup=markup,
    )
    await state.set_state(MyStates.waiting_for_russian_word)


@router.message(StateFilter(MyStates.waiting_for_russian_word))
async def add_word_get_russian(message: types.Message, state: FSMContext) -> None:
    """
    Validate and store the Russian word entered by the user.

    If the input is invalid or the user pressed cancel, the flow is
    terminated. Otherwise, the word is stored in FSM data and the
    state transitions to awaiting the English translation.

    Args:
        message: The incoming Telegram message containing the Russian word.
        state: The FSM context used to persist the add-word flow state.
    """
    new_word = message.text.strip()

    if new_word == CommandText.CANCEL:
        await message.answer("Добавление слова отменено.")
        await state.clear()
        await message.answer("Напишите /start для продолжения викторины.")
        return

    if not is_russian(new_word):
        await message.answer(
            "Пожалуйста, введите корректное русское слово (только русские буквы)."
        )
        return

    await state.update_data(new_word=new_word)
    await state.set_state(MyStates.waiting_for_translation_english)

    buttons = [types.KeyboardButton(text=CommandText.CANCEL)]
    markup = types.ReplyKeyboardMarkup(
        keyboard=[buttons],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.answer("Введите перевод на английский:", reply_markup=markup)


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
    translation = message.text.strip()

    if translation == CommandText.CANCEL:
        await message.answer("Добавление слова отменено.")
        await state.clear()
        await message.answer("Напишите /start для продолжения викторины.")
        return

    if not is_english(translation):
        await message.answer(
            "Пожалуйста, введите корректный английский перевод (только английские буквы)."
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

    await send_quiz(
        message=message,
        session=session,
        user=user,
        state=state,
    )


async def start_delete_word(
    message: types.Message,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    """
    Initiate the delete-word flow by listing the user's custom words.

    Fetches all words belonging to the user and displays them as a
    one-time reply keyboard. If the user has no words, the flow is
    terminated immediately with a notification.

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

    buttons = [types.KeyboardButton(text=word) for word in user_words]
    buttons.append(types.KeyboardButton(text=CommandText.CANCEL))
    keyboard = [[button] for button in buttons]
    markup = types.ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.answer("Выберите слово для удаления:", reply_markup=markup)
    await state.set_state(MyStates.waiting_for_word_to_delete)


@router.message(StateFilter(MyStates.waiting_for_word_to_delete))
async def delete_word_confirm(
    message: types.Message,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    """
    Confirm deletion of the selected word and resume the quiz.

    Attempts to delete the word identified by the user's input. If the
    word is not found, a notification is shown. In both cases, the FSM
    state is cleared and the quiz flow resumes.

    Args:
        message: The incoming Telegram message containing the word to delete.
        session: The active asynchronous SQLAlchemy session.
        user: The User instance who owns the word.
        state: The FSM context holding the delete-word flow state.
    """
    word_to_delete = message.text.strip()

    if word_to_delete == CommandText.CANCEL:
        await message.answer("Удаление отменено.")
        await state.clear()
        await send_quiz(
            message=message,
            session=session,
            user=user,
            state=state,
        )
        return

    deleted = await WordQueries.delete_user_word(
        session=session,
        user=user,
        word_user=word_to_delete,
    )

    if deleted:
        await message.answer(f"Слово '{word_to_delete}' удалено из вашего словаря.")
    else:
        await message.answer(f"Слово '{word_to_delete}' не найдено в вашем словаре.")

    await state.clear()
    await send_quiz(
        message=message,
        session=session,
        user=user,
        state=state,
    )
