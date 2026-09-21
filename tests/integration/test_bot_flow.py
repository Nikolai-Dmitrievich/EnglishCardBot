"""
Integration tests for the English Card Bot.

These tests verify the complete flow through the real Dispatcher,
database middleware, FSM, and SQLAlchemy queries. No network calls
are made - the bot operates with RecordingSession from conftest.
"""

from typing import Any

from sqlalchemy import func, select

from src.db.models import User, UserWord
from src.handlers.quiz import CommandText

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
            CommandText.NEXT,
            CommandText.ADD_WORD,
            CommandText.DELETE_WORD,
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
        assert await fsm_state["data"]() == data

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
        self, client: Any, fsm_state: Any
    ) -> None:
        await client.send("/start")
        await client.send(CommandText.ADD_WORD)
        client.clear()

        await client.send("array")

        assert "корректное русское слово" in client.last_text
        assert await fsm_state["state"]() == RUSSIAN_STATE

    async def test_non_english_translation_is_rejected(
        self, client: Any, fsm_state: Any
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

        assert "уже есть в вашем словаре" in client.texts[0]

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

        assert "удалено из вашего словаря" in client.texts[0]
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

        assert "Удаление отменено" in client.texts[0]
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

        assert "не найдено в вашем словаре" in client.texts[0]
        assert await session.scalar(select(func.count()).select_from(UserWord)) == 1


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

        for command in ("/start", "/stop", "/help"):
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
