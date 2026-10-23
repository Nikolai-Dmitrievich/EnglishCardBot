"""
Integration tests for the English Card Bot.

These tests verify the complete flow through the real Dispatcher,
database middleware, FSM, and SQLAlchemy queries. No network calls are
made - the bot operates with RecordingSession from conftest.
"""

from typing import Any

from aiogram.fsm.storage.base import StorageKey
from aiogram.methods import AnswerCallbackQuery
from sqlalchemy import func, select

from src.db.models import User, UserWord
from src.handlers.common import (
    ADD_WORD_BUTTON,
    DELETE_WORD_BUTTON,
    START_AI_QUIZ_BUTTON,
    START_QUIZ_BUTTON,
    STAT_BUTTON,
)
from src.handlers.common import ADD_WORD_DATA as MENU_ADD_WORD
from src.handlers.common import DELETE_WORD_DATA as MENU_DELETE_WORD
from src.handlers.common import START_QUIZ_DATA as MENU_QUIZ
from src.handlers.common import STAT_DATA as MENU_STAT
from src.handlers.quiz import QUIZ_LENGTH, STOP_QUIZ_DATA, CommandText
from tests.conftest import TEST_USER_ID

QUIZ_STATE = "MyStates:waiting_for_quiz_answer"
RUSSIAN_STATE = "MyStates:waiting_for_russian_word"
TRANSLATION_STATE = "MyStates:waiting_for_translation_english"
DELETE_STATE = "MyStates:waiting_for_word_to_delete"

MENU_BUTTONS = {
    START_QUIZ_BUTTON,
    START_AI_QUIZ_BUTTON,
    STAT_BUTTON,
    ADD_WORD_BUTTON,
    DELETE_WORD_BUTTON,
}


async def _start_quiz(client: Any, user_id: int = TEST_USER_ID) -> None:
    """Enter the quiz the way a user does: /start, then tap «Начать викторину».

    /start no longer fires a question on its own, so every quiz test has to
    press the menu button first.
    """
    await client.send("/start", user_id=user_id)
    await client.tap(MENU_QUIZ, user_id=user_id)


async def _add_word(
    client: Any, word: str, translation: str, user_id: int = TEST_USER_ID
) -> None:
    """Run the whole add-word flow starting from the action menu."""
    await client.send("/start", user_id=user_id)
    await client.tap(MENU_ADD_WORD, user_id=user_id)
    await client.send(word, user_id=user_id)
    await client.send(translation, user_id=user_id)


class TestStart:
    """Tests for the /start screen and its action menu."""

    async def test_start_creates_user_and_shows_menu(
        self, client: Any, session: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await client.send("/start")

        user = await session.scalar(select(User))
        assert user is not None
        assert user.telegram_username == "Nikolai"

        assert any("Добро пожаловать" in text for text in client.texts)
        assert set(client.keyboard) == MENU_BUTTONS

    async def test_start_does_not_fire_a_question(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        """Regression guard: /start is also the recovery command.

        Launching a question immediately buried the help text and left a
        half-finished flow impossible to escape, so the landing screen must
        wait for an explicit tap.
        """
        await client.send("/start")

        assert "Выбери правильный перевод слова:" not in client.texts
        assert await fsm_state["state"]() is None

    async def test_start_clears_a_stuck_flow(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await client.send("/start")
        await client.tap(MENU_ADD_WORD)
        assert await fsm_state["state"]() == RUSSIAN_STATE

        await client.send("/start")

        assert await fsm_state["state"]() is None

    async def test_menu_quiz_button_asks_question(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await client.send("/start")
        client.clear()

        await client.tap(MENU_QUIZ)

        assert "Выбери правильный перевод слова:" in client.last_text
        assert await fsm_state["state"]() == QUIZ_STATE

    async def test_question_keyboard_has_no_word_buttons(
        self, client: Any, common_words: Any
    ) -> None:
        """Word management moved to the menu, so the question row stays clean."""
        await _start_quiz(client)

        keyboard = client.keyboard
        nav_buttons = {"Пропустить ⏭", "⏹ Стоп"}
        options = set(keyboard) - nav_buttons

        assert nav_buttons <= set(keyboard)
        assert not (MENU_BUTTONS & set(keyboard))
        assert len(options) == 4
        # Options may carry a ✅ marker for the correct answer.
        option_texts = {opt.rstrip(" ✅") for opt in options}
        assert option_texts <= {translation for _, translation in common_words}

    async def test_start_sets_quiz_state(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await _start_quiz(client)

        assert await fsm_state["state"]() == QUIZ_STATE

    async def test_start_stores_correct_answer_in_state(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await _start_quiz(client)

        data = await fsm_state["data"]()
        # The keyboard may carry a ✅ marker on the correct option.
        assert any(data["correct_translation"] in btn for btn in client.keyboard), (
            f"{data['correct_translation']} not found in {client.keyboard}"
        )
        assert len(data["options"]) == 4

    async def test_quiz_without_words_asks_to_add_them(
        self, client: Any, fsm_state: Any
    ) -> None:
        await client.send("/start")
        client.clear()

        await client.tap(MENU_QUIZ)

        assert "В базе нет слов" in client.last_text
        assert await fsm_state["state"]() is None

    async def test_start_is_idempotent_for_second_user(
        self, client: Any, session: Any, common_words: Any
    ) -> None:
        await client.send("/start", user_id=111)
        await client.send("/start", user_id=222)

        assert await session.scalar(select(func.count()).select_from(User)) == 2

    async def test_quiz_command_starts_quiz_directly(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        """Regression guard: /quiz should start a quiz without showing the menu."""
        await client.send("/quiz")

        assert "Выбери правильный перевод слова:" in client.last_text
        assert await fsm_state["state"]() == QUIZ_STATE

    async def test_quiz_command_does_not_show_menu_keyboard(
        self, client: Any, common_words: Any
    ) -> None:
        """The /quiz command should not display the menu keyboard."""
        await client.send("/quiz")

        # The quiz keyboard should not contain any menu buttons.
        assert not (MENU_BUTTONS & set(client.keyboard))


class TestQuizAnswer:
    """Tests for quiz answer validation and state transitions."""

    async def test_correct_answer_confirms_and_asks_next(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await _start_quiz(client)
        client.clear()
        correct = (await fsm_state["data"]())["correct_translation"]

        await client.send(correct)

        assert "Отлично! ✅" in client.texts[0]
        assert "Выбери правильный перевод слова:" in client.texts[1]
        assert await fsm_state["state"]() == QUIZ_STATE

    async def test_wrong_answer_marks_option_and_keeps_question(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await _start_quiz(client)
        client.clear()
        data = await fsm_state["data"]()
        wrong = next(
            opt for opt in data["options"] if opt != data["correct_translation"]
        )

        await client.send(wrong)

        assert "Неправильно!" in client.last_text
        assert f"{wrong} ❌" in client.keyboard
        new_data = await fsm_state["data"]()
        # State should be updated: wrong answer increments incorrect_count.
        # question_number stays the same because send_quiz (which increments
        # it) is called *after* the keyboard is rebuilt.
        assert new_data["incorrect_count"] == data["incorrect_count"] + 1
        assert new_data["question_number"] == data["question_number"]
        # answered_indices is always reset to [] — send_quiz owns the counter.
        assert new_data["answered_indices"] == []
        # But the quiz is still active (state not cleared)
        assert new_data["target_word"] == data["target_word"]
        assert new_data["correct_index"] == data["correct_index"]

    async def test_wrong_answer_alert_shows_correct_option(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        """Regression guard: the alert toast must name the right answer.

        Only the inline-button path raises an alert; the text-answer fallback
        replies in the chat instead, so the question has to be tapped.
        """
        await _start_quiz(client)
        client.clear()
        data = await fsm_state["data"]()
        wrong_index = next(
            i
            for i, opt in enumerate(data["options"])
            if opt != data["correct_translation"]
        )

        await client.tap(f"ans:{wrong_index}")

        # ``RecordingSession.texts`` skips AnswerCallbackQuery (it carries no
        # chat_id), so the toast is read straight off the recorded methods.
        alert = next(
            (
                method.text
                for method in client.bot.session.sent
                if isinstance(method, AnswerCallbackQuery) and method.show_alert
            ),
            None,
        )

        assert alert is not None
        assert "Неверно" in alert
        assert data["correct_translation"] in alert

    async def test_wrong_answer_does_not_change_state(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await _start_quiz(client)
        data_before = await fsm_state["data"]()

        await client.send(
            next(
                opt
                for opt in data_before["options"]
                if opt != data_before["correct_translation"]
            )
        )

        assert await fsm_state["state"]() == QUIZ_STATE

    async def test_free_text_is_rejected(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await _start_quiz(client)
        client.clear()

        await client.send("я не знаю")

        assert "выберите вариант из кнопок" in client.last_text
        assert len(client.texts) == 1

    async def test_next_button_asks_new_question(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await _start_quiz(client)
        client.clear()

        await client.send(CommandText.NEXT)

        assert "Выбери правильный перевод слова:" in client.last_text
        assert await fsm_state["state"]() == QUIZ_STATE

    async def test_help_during_quiz(self, client: Any, common_words: Any) -> None:
        await _start_quiz(client)
        client.clear()

        await client.send("/help")

        assert "Доступные команды" in client.texts[0]
        assert set(client.keyboard) == MENU_BUTTONS

    async def test_menu_still_reachable_during_quiz(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        """Word management left the question keyboard, but not the bot.

        The menu lives in common.router, registered before quiz.router, so its
        namespaced payloads keep working while a question is pending.
        """
        await _start_quiz(client)
        client.clear()

        await client.tap(MENU_ADD_WORD)

        assert "Введите новое русское слово" in client.last_text
        assert await fsm_state["state"]() == RUSSIAN_STATE


class TestAddWord:
    """Tests for the word addition flow and validation."""

    async def test_full_flow(
        self, client: Any, session: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await client.send("/start")
        client.clear()
        await client.tap(MENU_ADD_WORD)

        assert "Введите новое русское слово" in client.last_text
        assert await fsm_state["state"]() == RUSSIAN_STATE

        await client.send("массив")

        assert "Введите перевод на английский" in client.last_text
        assert await fsm_state["state"]() == TRANSLATION_STATE

        client.clear()
        await client.send("array")

        assert "добавлено в ваш словарь" in client.texts[0]
        assert await fsm_state["state"]() is None
        assert not any(
            "Выбери правильный перевод слова:" in text for text in client.texts
        )

        stored = await session.scalar(
            select(UserWord).where(UserWord.word_user == "массив")
        )
        assert stored is not None
        assert stored.translation_user == "array"

    async def test_non_russian_word_is_rejected(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await client.send("/start")
        await client.tap(MENU_ADD_WORD)
        client.clear()

        await client.send("array")

        assert "корректное русское слово" in client.last_text
        assert await fsm_state["state"]() == RUSSIAN_STATE

    async def test_non_english_translation_is_rejected(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await client.send("/start")
        await client.tap(MENU_ADD_WORD)
        await client.send("массив")
        client.clear()

        await client.send("массив")

        assert "корректный английский перевод" in client.last_text
        assert await fsm_state["state"]() == TRANSLATION_STATE

    async def test_duplicate_word_is_reported(
        self, client: Any, session: Any, common_words: Any
    ) -> None:
        await _add_word(client, "массив", "array")
        client.clear()

        await client.send("/start")
        await client.tap(MENU_ADD_WORD)
        await client.send("массив")
        await client.send("vector")

        assert any("уже есть в вашем словаре" in text for text in client.texts)

    async def test_missing_russian_word_in_state_reports_error(
        self,
        client: Any,
        bot: Any,
        dp: Any,
        common_words: Any,
        fsm_state: Any,
    ) -> None:
        # Regression guard: if FSM data loses the buffered Russian word while
        # still in the translation state, the handler must fail gracefully
        # instead of persisting a word with a None source.
        await client.send("/start")
        await client.tap(MENU_ADD_WORD)
        await client.send("массив")

        key = StorageKey(bot_id=bot.id, chat_id=TEST_USER_ID, user_id=TEST_USER_ID)
        await dp.storage.set_data(key, {})
        client.clear()

        await client.send("array")

        assert "не удалось получить русское слово" in client.texts[0]
        assert await fsm_state["state"]() is None

    async def test_no_cancel_keyboard_above_the_input_line(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        """Word entry must not cover the input line with a reply keyboard.

        Management actions live in the inline menu now, so the add-word
        prompts are plain messages and /stop is the only way out.
        """
        await client.send("/start")
        client.clear()

        await client.tap(MENU_ADD_WORD)
        await client.send("массив")

        assert client.keyboard == []

    async def test_stop_aborts_add_flow_on_russian_step(
        self, client: Any, session: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await client.send("/start")
        await client.tap(MENU_ADD_WORD)
        client.clear()

        await client.send("/stop")

        assert "Викторина остановлена" in client.texts[0]
        assert await fsm_state["state"]() is None
        assert await session.scalar(select(func.count()).select_from(UserWord)) == 0

    async def test_stop_aborts_add_flow_on_translation_step(
        self, client: Any, session: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await client.send("/start")
        await client.tap(MENU_ADD_WORD)
        await client.send("массив")
        client.clear()

        await client.send("/stop")

        assert "Викторина остановлена" in client.texts[0]
        assert await fsm_state["state"]() is None
        assert await session.scalar(select(func.count()).select_from(UserWord)) == 0


class TestDeleteWord:
    """Tests for the word deletion flow and edge cases."""

    async def test_full_flow(
        self, client: Any, session: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await _add_word(client, "массив", "array")
        client.clear()

        await client.send("/start")
        await client.tap(MENU_DELETE_WORD)

        assert "Выберите слово для удаления" in client.last_text
        assert client.keyboard == ["массив"]
        assert await fsm_state["state"]() == DELETE_STATE

        await client.tap("delete:0")

        assert any("удалено из вашего словаря" in text for text in client.texts)
        assert await session.scalar(select(func.count()).select_from(UserWord)) == 0

    async def test_delete_without_words(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await client.send("/start")
        client.clear()

        await client.tap(MENU_DELETE_WORD)

        assert "нет добавленных слов" in client.last_text
        assert await fsm_state["state"]() is None

    async def test_word_list_is_inline_only(
        self, client: Any, session: Any, common_words: Any, fsm_state: Any
    ) -> None:
        """The picker must not cover the input line with a reply keyboard."""
        await _add_word(client, "массив", "array")
        client.clear()

        await client.send("/start")
        await client.tap(MENU_DELETE_WORD)

        assert client.keyboard == ["массив"]

    async def test_stop_keeps_word(
        self, client: Any, session: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await _add_word(client, "массив", "array")
        client.clear()

        await client.send("/start")
        await client.tap(MENU_DELETE_WORD)
        await client.send("/stop")

        assert any("Викторина остановлена" in text for text in client.texts)
        assert await fsm_state["state"]() is None
        assert await session.scalar(select(func.count()).select_from(UserWord)) == 1

    async def test_typed_word_is_not_deleted(
        self, client: Any, session: Any, common_words: Any, fsm_state: Any
    ) -> None:
        """Deletion is button-only: free text must not remove anything."""
        await _add_word(client, "массив", "array")
        client.clear()

        await client.send("/start")
        await client.tap(MENU_DELETE_WORD)
        await client.send("массив")

        assert "Выберите слово кнопкой" in client.last_text
        assert await session.scalar(select(func.count()).select_from(UserWord)) == 1

    async def test_stale_button_is_reported(
        self, client: Any, session: Any, common_words: Any, fsm_state: Any
    ) -> None:
        """A keyboard from an earlier round must not delete by position."""
        await _add_word(client, "массив", "array")
        client.clear()

        await client.send("/start")
        await client.tap(MENU_DELETE_WORD)
        await client.send("/stop")
        client.clear()

        await client.tap("delete:0")

        assert client.texts == []
        assert await session.scalar(select(func.count()).select_from(UserWord)) == 1


class TestQuizRestart:
    """Regression tests for the results screen and its «Начать заново» button.

    The results screen is sent right after ``state.clear()``, so its buttons
    are tapped with an empty FSM. Handlers bound to
    ``waiting_for_quiz_answer`` never observe them — these tests pin down
    that the taps are still routed and start a fresh quiz.
    """

    async def _finish_quiz(self, client: Any, fsm_state: Any) -> None:
        """Answer every question correctly so the results screen is shown."""
        for _ in range(QUIZ_LENGTH):
            data = await fsm_state["data"]()
            await client.tap(f"ans:{data['correct_index']}")

    async def test_quiz_ends_with_results_screen(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await _start_quiz(client)
        await self._finish_quiz(client, fsm_state)

        assert "Квиз завершён" in client.last_text
        assert "🔄 Начать заново" in client.keyboard
        assert await fsm_state["state"]() is None

    async def test_results_screen_has_no_word_buttons(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        """Word management now lives in the menu only.

        A leftover «🗑 Слово» here would be a dead button: the screen is sent
        after ``state.clear()``, and the stateless handler that used to serve
        it is gone.
        """
        await _start_quiz(client)
        await self._finish_quiz(client, fsm_state)

        assert not (MENU_BUTTONS & set(client.keyboard))

    async def test_restart_button_starts_new_quiz(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await _start_quiz(client)
        await self._finish_quiz(client, fsm_state)
        client.clear()

        await client.tap("next:restart")

        assert "Выбери правильный перевод слова:" in client.last_text
        assert "Вопрос 1/10" in client.last_text
        assert await fsm_state["state"]() == QUIZ_STATE

    async def test_restart_resets_progress_counters(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await _start_quiz(client)
        await self._finish_quiz(client, fsm_state)

        await client.tap("next:restart")

        data = await fsm_state["data"]()
        assert data["question_number"] == 1
        assert data["correct_count"] == 0
        assert data["incorrect_count"] == 0

    async def test_restart_is_repeatable(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        """A restarted quiz must reach its results screen again."""
        await _start_quiz(client)
        await self._finish_quiz(client, fsm_state)

        await client.tap("next:restart")
        await self._finish_quiz(client, fsm_state)

        assert "Квиз завершён" in client.last_text
        assert await fsm_state["state"]() is None


class TestCommonCommands:
    """Tests for /stop, /help, and unhandled message scenarios."""

    async def test_stop_clears_state(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await _start_quiz(client)

        await client.send("/stop")

        assert "Викторина остановлена" in client.last_text
        assert await fsm_state["state"]() is None

    async def test_help_lists_commands(self, client: Any) -> None:
        await client.send("/help")

        for command in ("/start", "/quiz_ai", "/stat", "/stop", "/help"):
            assert command in client.texts[0]

    async def test_help_offers_the_menu(self, client: Any) -> None:
        await client.send("/help")

        assert set(client.keyboard) == MENU_BUTTONS

    async def test_menu_stat_button_opens_period_picker(
        self, client: Any, fsm_state: Any
    ) -> None:
        """The «Статистика» entry point must reach the /stat flow."""
        await client.send("/start")
        client.clear()

        await client.tap(MENU_STAT)

        assert "Выберите период" in client.last_text
        assert await fsm_state["state"]() is None

    async def test_message_without_state_and_command_is_unhandled(
        self, client: Any, common_words: Any
    ) -> None:
        client.clear()

        await client.send("просто текст")

        assert client.texts == []


class TestUsersIsolation:
    """Tests for user data isolation and FSM state separation."""

    async def test_words_are_not_shared(
        self, client: Any, session: Any, common_words: Any
    ) -> None:
        await _add_word(client, "массив", "array", user_id=111)
        client.clear()

        await client.send("/start", user_id=222)
        await client.tap(MENU_DELETE_WORD, user_id=222)

        assert "нет добавленных слов" in client.last_text

    async def test_states_are_not_shared(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await client.send("/start", user_id=111)
        await client.tap(MENU_ADD_WORD, user_id=111)

        await client.send("/start", user_id=222)

        assert await fsm_state["state"](user_id=111) == RUSSIAN_STATE
        assert await fsm_state["state"](user_id=222) is None


class TestStopQuizButton:
    """Tests for the inline «⏹ Стоп» button that stops a running quiz."""

    async def test_stop_button_clears_state_and_shows_message(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await _start_quiz(client)
        client.clear()

        await client.tap(STOP_QUIZ_DATA)

        assert "Викторина остановлена" in client.last_text
        assert await fsm_state["state"]() is None

    async def test_stop_button_removes_keyboard(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await _start_quiz(client)
        client.clear()

        await client.tap(STOP_QUIZ_DATA)

        # The stop handler sends a new message (not an edit), so the last
        # message should have no inline keyboard.
        assert client.keyboard == []


class TestQuizNoRepeats:
    """Words must not repeat within a single quiz session until all are exhausted."""

    async def test_words_do_not_repeat_within_one_quiz(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        """With 4 words and QUIZ_LENGTH=10 the pool must cycle, but never
        repeat a word while unseen ones remain."""
        await _start_quiz(client)
        client.clear()

        seen_words: set[str] = set()
        # Answer all 4 words correctly — the first 4 questions must each
        # present a different word.
        for _ in range(4):
            data = await fsm_state["data"]()
            seen_words.add(data["target_word"])
            await client.tap(f"ans:{data['correct_index']}")

        assert len(seen_words) == 4, "Каждое слово должно появиться ровно один раз"

    async def test_quiz_cycles_after_all_words_seen(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        """Once every word has been asked, the quiz must continue by
        reusing words — otherwise a small dictionary would end the quiz
        early."""
        await _start_quiz(client)
        client.clear()

        # Answer all 4 words correctly.
        for _ in range(4):
            data = await fsm_state["data"]()
            await client.tap(f"ans:{data['correct_index']}")

        # The quiz must still be running (question 5 of 10).
        assert "Вопрос 5/10" in client.last_text
