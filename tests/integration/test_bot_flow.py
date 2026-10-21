"""
Integration tests for the English Card Bot.

These tests verify the complete flow through the real Dispatcher,
database middleware, FSM, and SQLAlchemy queries. No network calls are
made - the bot operates with RecordingSession from conftest.
"""

from typing import Any

from aiogram.fsm.storage.base import StorageKey
from sqlalchemy import func, select

from src.db.models import User, UserWord
from src.handlers.quiz import QUIZ_LENGTH, CommandText
from tests.conftest import TEST_USER_ID

QUIZ_STATE = "MyStates:waiting_for_quiz_answer"
RUSSIAN_STATE = "MyStates:waiting_for_russian_word"
TRANSLATION_STATE = "MyStates:waiting_for_translation_english"
DELETE_STATE = "MyStates:waiting_for_word_to_delete"


class TestStart:
    """Tests for the /start command and initial quiz question generation."""

    async def test_start_creates_user_and_asks_question(
        self, client: Any, session: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await client.send("/start")

        user = await session.scalar(select(User))
        assert user is not None
        assert user.telegram_username == "Nikolai"

        assert any("Добро пожаловать" in text for text in client.texts)
        assert "Выбери правильный перевод слова:" in client.last_text

    async def test_start_shows_four_options_and_service_buttons(
        self, client: Any, common_words: Any
    ) -> None:
        await client.send("/start")

        keyboard = client.keyboard
        service_buttons = {
            "📊 Результаты",
            "Пропустить ⏭",
            "➕ Слово",
            "🗑 Слово",
        }
        options = set(keyboard) - service_buttons

        assert service_buttons <= set(keyboard)
        assert len(options) == 4
        assert options <= {translation for _, translation in common_words}

    async def test_start_sets_quiz_state(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await client.send("/start")

        assert await fsm_state["state"]() == QUIZ_STATE

    async def test_start_stores_correct_answer_in_state(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await client.send("/start")

        data = await fsm_state["data"]()
        assert data["correct_translation"] in client.keyboard
        assert len(data["options"]) == 4

    async def test_start_without_words_asks_to_add_them(
        self, client: Any, fsm_state: Any
    ) -> None:
        await client.send("/start")

        assert "В базе нет слов" in client.last_text
        assert await fsm_state["state"]() is None

    async def test_start_is_idempotent_for_second_user(
        self, client: Any, session: Any, common_words: Any
    ) -> None:
        await client.send("/start", user_id=111)
        await client.send("/start", user_id=222)

        assert await session.scalar(select(func.count()).select_from(User)) == 2


class TestQuizAnswer:
    """Tests for quiz answer validation and state transitions."""

    async def test_correct_answer_confirms_and_asks_next(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await client.send("/start")
        client.clear()
        correct = (await fsm_state["data"]())["correct_translation"]

        await client.send(correct)

        assert "Отлично! ✅" in client.texts[0]
        assert "Выбери правильный перевод слова:" in client.texts[1]
        assert await fsm_state["state"]() == QUIZ_STATE

    async def test_wrong_answer_marks_option_and_keeps_question(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await client.send("/start")
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

    async def test_wrong_answer_does_not_change_state(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await client.send("/start")
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
        await client.send("/start")
        client.clear()

        await client.send("я не знаю")

        assert "выберите вариант из кнопок" in client.last_text
        assert len(client.texts) == 1

    async def test_next_button_asks_new_question(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await client.send("/start")
        client.clear()

        await client.send(CommandText.NEXT)

        assert "Выбери правильный перевод слова:" in client.last_text
        assert await fsm_state["state"]() == QUIZ_STATE

    async def test_help_during_quiz(self, client: Any, common_words: Any) -> None:
        await client.send("/start")
        client.clear()

        await client.send("/help")

        assert "Доступные команды" in client.last_text


class TestAddWord:
    """Tests for the word addition flow and validation."""

    async def test_full_flow(
        self, client: Any, session: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await client.send("/start")
        await client.send(CommandText.ADD_WORD)

        assert "Введите новое русское слово" in client.last_text
        assert await fsm_state["state"]() == RUSSIAN_STATE

        await client.send("массив")

        assert "Введите перевод на английский" in client.last_text
        assert await fsm_state["state"]() == TRANSLATION_STATE

        client.clear()
        await client.send("array")

        assert "добавлено в ваш словарь" in client.texts[0]
        assert "Выбери правильный перевод слова:" in client.texts[1]

        stored = await session.scalar(
            select(UserWord).where(UserWord.word_user == "массив")
        )
        assert stored is not None
        assert stored.translation_user == "array"

    async def test_non_russian_word_is_rejected(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await client.send("/start")
        await client.send(CommandText.ADD_WORD)
        client.clear()

        await client.send("array")

        assert "корректное русское слово" in client.last_text
        assert await fsm_state["state"]() == RUSSIAN_STATE

    async def test_non_english_translation_is_rejected(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await client.send("/start")
        await client.send(CommandText.ADD_WORD)
        await client.send("массив")
        client.clear()

        await client.send("массив")

        assert "корректный английский перевод" in client.last_text
        assert await fsm_state["state"]() == TRANSLATION_STATE

    async def test_duplicate_word_is_reported(
        self, client: Any, session: Any, common_words: Any
    ) -> None:
        await client.send("/start")
        await client.send(CommandText.ADD_WORD)
        await client.send("массив")
        await client.send("array")
        client.clear()

        await client.send(CommandText.ADD_WORD)
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
        await client.send(CommandText.ADD_WORD)
        await client.send("массив")

        key = StorageKey(bot_id=bot.id, chat_id=TEST_USER_ID, user_id=TEST_USER_ID)
        await dp.storage.set_data(key, {})
        client.clear()

        await client.send("array")

        assert "не удалось получить русское слово" in client.texts[0]
        assert await fsm_state["state"]() is None

    async def test_cancel_on_russian_step(
        self, client: Any, session: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await client.send("/start")
        await client.send(CommandText.ADD_WORD)
        client.clear()

        await client.send(CommandText.CANCEL)

        assert "Добавление слова отменено" in client.texts[0]
        assert await fsm_state["state"]() is None
        assert await session.scalar(select(func.count()).select_from(UserWord)) == 0

    async def test_cancel_on_translation_step(
        self, client: Any, session: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await client.send("/start")
        await client.send(CommandText.ADD_WORD)
        await client.send("массив")
        client.clear()

        await client.send(CommandText.CANCEL)

        assert "Добавление слова отменено" in client.texts[0]
        assert await fsm_state["state"]() is None
        assert await session.scalar(select(func.count()).select_from(UserWord)) == 0


class TestDeleteWord:
    """Tests for the word deletion flow and edge cases."""

    async def test_full_flow(
        self, client: Any, session: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await client.send("/start")
        await client.send(CommandText.ADD_WORD)
        await client.send("массив")
        await client.send("array")
        client.clear()

        await client.send(CommandText.DELETE_WORD)

        assert "Выберите слово для удаления" in client.texts[0]
        assert "массив" in client.keyboard
        assert await fsm_state["state"]() == DELETE_STATE

        await client.send("массив")

        assert any("удалено из вашего словаря" in text for text in client.texts)
        assert await session.scalar(select(func.count()).select_from(UserWord)) == 0

    async def test_delete_without_words(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await client.send("/start")
        client.clear()

        await client.send(CommandText.DELETE_WORD)

        assert "нет добавленных слов" in client.last_text
        assert await fsm_state["state"]() == QUIZ_STATE

    async def test_cancel_keeps_word(
        self, client: Any, session: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await client.send("/start")
        await client.send(CommandText.ADD_WORD)
        await client.send("массив")
        await client.send("array")
        client.clear()

        await client.send(CommandText.DELETE_WORD)
        await client.send(CommandText.CANCEL)

        assert any("Удаление отменено" in text for text in client.texts)
        assert await session.scalar(select(func.count()).select_from(UserWord)) == 1

    async def test_unknown_word_is_reported(
        self, client: Any, session: Any, common_words: Any
    ) -> None:
        await client.send("/start")
        await client.send(CommandText.ADD_WORD)
        await client.send("массив")
        await client.send("array")
        client.clear()

        await client.send(CommandText.DELETE_WORD)
        await client.send("несуществующее слово")

        assert any("не найдено в вашем словаре" in text for text in client.texts)
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
        await client.send("/start")
        await self._finish_quiz(client, fsm_state)

        assert "Квиз завершён" in client.last_text
        assert "🔄 Начать заново" in client.keyboard
        assert await fsm_state["state"]() is None

    async def test_restart_button_starts_new_quiz(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await client.send("/start")
        await self._finish_quiz(client, fsm_state)
        client.clear()

        await client.tap("next:restart")

        assert "Выбери правильный перевод слова:" in client.last_text
        assert "Вопрос 1/10" in client.last_text
        assert await fsm_state["state"]() == QUIZ_STATE

    async def test_restart_resets_progress_counters(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await client.send("/start")
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
        await client.send("/start")
        await self._finish_quiz(client, fsm_state)

        await client.tap("next:restart")
        await self._finish_quiz(client, fsm_state)

        assert "Квиз завершён" in client.last_text
        assert await fsm_state["state"]() is None

    async def test_delete_word_button_works_on_results_screen(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await client.send("/start")
        await client.send(CommandText.ADD_WORD)
        await client.send("массив")
        await client.send("array")
        await self._finish_quiz(client, fsm_state)
        client.clear()

        await client.tap("delete_word")

        assert "Выберите слово для удаления" in client.last_text
        assert await fsm_state["state"]() == DELETE_STATE


class TestCommonCommands:
    """Tests for /stop, /help, and unhandled message scenarios."""

    async def test_stop_clears_state(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await client.send("/start")

        await client.send("/stop")

        assert "Викторина остановлена" in client.last_text
        assert await fsm_state["state"]() is None

    async def test_help_lists_commands(self, client: Any) -> None:
        await client.send("/help")

        for command in ("/start", "/quiz_ai", "/stat", "/stop", "/help"):
            assert command in client.last_text

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
        await client.send("/start", user_id=111)
        await client.send(CommandText.ADD_WORD, user_id=111)
        await client.send("массив", user_id=111)
        await client.send("array", user_id=111)
        client.clear()

        await client.send("/start", user_id=222)
        await client.send(CommandText.DELETE_WORD, user_id=222)

        assert "нет добавленных слов" in client.last_text

    async def test_states_are_not_shared(
        self, client: Any, common_words: Any, fsm_state: Any
    ) -> None:
        await client.send("/start", user_id=111)
        await client.send(CommandText.ADD_WORD, user_id=111)

        await client.send("/start", user_id=222)

        assert await fsm_state["state"](user_id=111) == RUSSIAN_STATE
        assert await fsm_state["state"](user_id=222) == QUIZ_STATE
