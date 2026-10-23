"""Integration tests for the AI quiz flow.

The LLM completion and the MCP session factory are stubbed, so the tests
exercise the real router stack (ai_quiz + quiz + words) without touching
Ollama or PostgreSQL.
"""

import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from src.db.models import WordError
from src.db.queries import UserQueries, WordQueries
from src.handlers.common import ADD_WORD_DATA as MENU_ADD_WORD
from src.handlers.common import START_AI_QUIZ_DATA as MENU_AI_QUIZ
from src.handlers.common import START_QUIZ_DATA as MENU_QUIZ
from src.mcp.tools import configure_session_pool
from tests.conftest import TEST_USER_ID

AI_QUIZ_STATE = "MyStates:waiting_for_ai_quiz_answer"


async def _word_errors(session: Any) -> list[WordError]:
    """Fetch every recorded error row, ignoring the period filters."""
    result = await session.execute(select(WordError))
    return list(result.scalars().all())


def _quiz_payload(question_count: int = 2, *, leak_answer: bool = False) -> str:
    """Build a QuizResponse-shaped JSON payload for the stubbed LLM.

    The default payload satisfies ``validate_quiz``: every sentence carries the
    ``____`` blank and never spells out its own correct option. ``leak_answer``
    reproduces the failure the post-validation exists to catch.
    """
    options = ["apple", "banana", "cherry", "date"]
    questions = [
        {
            "question": (
                f"I keep the {options[i % 4]} in my bag for lesson {i}."
                if leak_answer
                else f"I keep the ____ in my bag for lesson {i}."
            ),
            "options": options,
            "correct_index": i % 4,
            "explanation_ru": f"Пояснение {i}",
        }
        for i in range(question_count)
    ]
    return json.dumps(
        {
            "intro": "Привет! Это квиз.",
            "questions": questions,
            "summary": "Добавь больше слов через /add.",
        },
        ensure_ascii=False,
    )


@pytest.fixture
def stubbed_llm(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace the Ollama call with a canned response and record each prompt."""
    calls: list[dict[str, Any]] = []

    async def fake_complete_with_format(
        messages: list[dict],
        format_schema: dict,
        timeout: int,
    ) -> str:
        calls.append({"messages": messages, "schema": format_schema})
        return _quiz_payload(question_count=len(calls) and 2)

    monkeypatch.setattr(
        "src.handlers.ai_quiz.llm_client.complete_with_format",
        fake_complete_with_format,
    )
    return calls


@pytest.fixture
def stubbed_llm_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Stub the LLM with a scripted list of payloads, one per call.

    Lets a test make the first attempt leak the answer and the second one be
    well-formed, which is exactly the retry path post-validation takes.
    """
    calls: list[dict[str, Any]] = []
    payloads: list[str] = []

    async def fake_complete_with_format(
        messages: list[dict],
        format_schema: dict,
        timeout: int,
    ) -> str:
        calls.append({"messages": messages, "schema": format_schema})
        index = min(len(calls) - 1, len(payloads) - 1)
        return payloads[index]

    monkeypatch.setattr(
        "src.handlers.ai_quiz.llm_client.complete_with_format",
        fake_complete_with_format,
    )
    return calls, payloads


@pytest.fixture
def ai_quiz_env(
    session_factory: Any,
    common_words: list[tuple[str, str]],
) -> Generator[list[tuple[str, str]]]:
    """Point the MCP tools at the in-memory test database."""
    configure_session_pool(session_factory)
    yield common_words
    configure_session_pool(None)


class TestAIQuizGeneration:
    """The /quiz_ai command must build a quiz from every available word source."""

    async def test_quiz_command_prefix_does_not_match_quiz_ai(
        self,
        client_ai: Any,
        ai_quiz_env: Any,
        stubbed_llm: list[dict[str, Any]],
        fsm_state: Any,
    ) -> None:
        """Regression guard: ``Command("quiz")`` must not swallow ``/quiz_ai``.

        Both commands live in different routers and ``common.router`` is
        registered first, so a prefix-matching filter would start the plain
        quiz and the AI flow would never run.
        """
        await client_ai.send("/quiz_ai")

        assert "Выбери правильный перевод слова:" not in client_ai.texts
        assert await fsm_state["state"]() == AI_QUIZ_STATE
        assert len(stubbed_llm) == 1

    async def test_quiz_ai_starts_quiz_and_sets_state(
        self,
        client_ai: Any,
        ai_quiz_env: Any,
        stubbed_llm: list[dict[str, Any]],
        fsm_state: Any,
    ) -> None:
        await client_ai.send("/quiz_ai")

        assert "Вопрос 1/2" in client_ai.last_text
        assert await fsm_state["state"]() == AI_QUIZ_STATE

    async def test_prompt_includes_common_dictionary_words(
        self,
        client_ai: Any,
        ai_quiz_env: list[tuple[str, str]],
        stubbed_llm: list[dict[str, Any]],
    ) -> None:
        """Regression guard: /quiz_ai used to see only user-added words.

        A brand-new user has no custom words at all, so without the common
        dictionary the prompt collapses to empty lists and the model has
        nothing to build questions from.
        """
        await client_ai.send("/quiz_ai")

        prompt = stubbed_llm[0]["messages"][0]["content"]
        for _, translation in ai_quiz_env:
            assert translation in prompt

    async def test_second_generation_still_receives_words(
        self,
        client_ai: Any,
        ai_quiz_env: Any,
        stubbed_llm: list[dict[str, Any]],
    ) -> None:
        await client_ai.send("/quiz_ai")
        await client_ai.send("/quiz_ai")

        assert len(stubbed_llm) == 2

    async def test_menu_button_starts_ai_quiz(
        self,
        client_ai: Any,
        ai_quiz_env: Any,
        stubbed_llm: list[dict[str, Any]],
        fsm_state: Any,
    ) -> None:
        """/quiz_ai is also reachable as a button on the /start and /help menu.

        The command handler refuses messages without text, so the button has
        to enter through a path that does not check the payload length.
        """
        await client_ai.send("/start")
        client_ai.clear()

        await client_ai.tap(MENU_AI_QUIZ)

        assert "Вопрос 1/2" in client_ai.last_text
        assert await fsm_state["state"]() == AI_QUIZ_STATE
        assert len(stubbed_llm) == 1


class TestAIQuizPostValidation:
    """A question that spells out its own answer must never reach the user.

    ``validate_quiz`` catches the failure mode the prompt cannot fully prevent:
    the model returns a well-formed sentence with the correct option already in
    it, which makes the quiz trivial. Such a quiz is regenerated once, and only
    then does the flow fall back to the plain quiz.
    """

    async def test_leaked_answer_triggers_regeneration(
        self,
        client_ai: Any,
        ai_quiz_env: Any,
        stubbed_llm_sequence: tuple[list[dict[str, Any]], list[str]],
        fsm_state: Any,
    ) -> None:
        calls, payloads = stubbed_llm_sequence
        payloads.append(_quiz_payload(leak_answer=True))
        payloads.append(_quiz_payload())

        await client_ai.send("/quiz_ai")

        assert len(calls) == 2
        assert "Вопрос 1/2" in client_ai.last_text
        assert await fsm_state["state"]() == AI_QUIZ_STATE

    async def test_retry_prompt_names_the_broken_rule(
        self,
        client_ai: Any,
        ai_quiz_env: Any,
        stubbed_llm_sequence: tuple[list[dict[str, Any]], list[str]],
    ) -> None:
        """The second request must not be a blind resend of the same prompt."""
        calls, payloads = stubbed_llm_sequence
        payloads.append(_quiz_payload(leak_answer=True))
        payloads.append(_quiz_payload())

        await client_ai.send("/quiz_ai")

        first_prompt = calls[0]["messages"][0]["content"]
        second_prompt = calls[1]["messages"][0]["content"]
        assert "____" in second_prompt[len(first_prompt) :]

    async def test_leaked_answer_falls_back_after_second_attempt(
        self,
        client_ai: Any,
        ai_quiz_env: Any,
        stubbed_llm_sequence: tuple[list[dict[str, Any]], list[str]],
        fsm_state: Any,
    ) -> None:
        calls, payloads = stubbed_llm_sequence
        payloads.append(_quiz_payload(leak_answer=True))

        await client_ai.send("/quiz_ai")

        assert len(calls) == 2
        assert "Не удалось создать AI-квиз" in client_ai.last_text
        assert await fsm_state["state"]() is None

    async def test_valid_quiz_is_not_regenerated(
        self,
        client_ai: Any,
        ai_quiz_env: Any,
        stubbed_llm_sequence: tuple[list[dict[str, Any]], list[str]],
    ) -> None:
        calls, payloads = stubbed_llm_sequence
        payloads.append(_quiz_payload())

        await client_ai.send("/quiz_ai")

        assert len(calls) == 1


class TestAIQuizRestart:
    """Finishing a quiz must leave the «Новый квиз» button functional."""

    async def _finish_quiz(self, client_ai: Any, question_count: int = 2) -> None:
        """Answer every question so the results screen is shown.

        The question number is part of the callback payload, so each tap has
        to carry its own index — reusing ``aians:1:0`` hits an already
        answered question and is silently ignored.
        """
        for question_no in range(1, question_count + 1):
            await client_ai.tap(f"aians:{question_no}:0")

    async def test_answering_last_question_shows_results(
        self,
        client_ai: Any,
        ai_quiz_env: Any,
        stubbed_llm: list[dict[str, Any]],
        fsm_state: Any,
    ) -> None:
        await client_ai.send("/quiz_ai")
        client_ai.clear()

        await self._finish_quiz(client_ai)

        assert "AI-квиз завершён" in client_ai.last_text
        assert await fsm_state["state"]() is None

    async def test_new_quiz_button_restarts_after_completion(
        self,
        client_ai: Any,
        ai_quiz_env: Any,
        stubbed_llm: list[dict[str, Any]],
        fsm_state: Any,
    ) -> None:
        """Regression guard: after state.clear() the restart tap was dropped.

        The results screen is sent right after the state is cleared, so the
        handler for «Новый квиз» must not depend on an active FSM state.
        """
        await client_ai.send("/quiz_ai")
        await self._finish_quiz(client_ai)
        client_ai.clear()

        await client_ai.tap("new_quiz")

        assert "Вопрос 1/2" in client_ai.last_text
        assert await fsm_state["state"]() == AI_QUIZ_STATE

    async def test_add_word_button_works_on_results_screen(
        self,
        client_ai: Any,
        ai_quiz_env: Any,
        stubbed_llm: list[dict[str, Any]],
        fsm_state: Any,
    ) -> None:
        await client_ai.send("/quiz_ai")
        await self._finish_quiz(client_ai)
        client_ai.clear()

        await client_ai.tap("add_word")

        assert "Введите новое русское слово" in client_ai.last_text
        assert await fsm_state["state"]() == "MyStates:waiting_for_russian_word"


class TestAIQuizStop:
    """The inline «⏹ Стоп» button must end an AI quiz session."""

    async def test_stop_button_clears_state_and_shows_message(
        self,
        client_ai: Any,
        ai_quiz_env: Any,
        stubbed_llm: list[dict[str, Any]],
        fsm_state: Any,
    ) -> None:
        await client_ai.send("/quiz_ai")
        client_ai.clear()

        await client_ai.tap("stop_quiz")

        assert "Викторина остановлена" in client_ai.last_text
        assert await fsm_state["state"]() is None

    async def test_stop_button_is_rendered_on_question(
        self,
        client_ai: Any,
        ai_quiz_env: Any,
        stubbed_llm: list[dict[str, Any]],
    ) -> None:
        await client_ai.send("/quiz_ai")

        assert "⏹ Стоп" in client_ai.keyboard

    async def test_stop_button_does_not_break_plain_quiz(
        self,
        client_ai: Any,
        ai_quiz_env: Any,
        stubbed_llm: list[dict[str, Any]],
        fsm_state: Any,
    ) -> None:
        """Both flows share the ``stop_quiz`` payload, only the state differs.

        The AI handler is bound to ``waiting_for_ai_quiz_answer``, so stopping
        an AI quiz must not leave the plain quiz unable to serve its own tap.
        """
        await client_ai.send("/quiz_ai")
        await client_ai.tap("stop_quiz")

        assert await fsm_state["state"]() is None


class TestAIQuizIsolation:
    """AI quiz callbacks must not leak into the plain quiz and vice versa."""

    async def test_plain_quiz_answer_not_swallowed_by_ai_router(
        self,
        client_ai: Any,
        ai_quiz_env: Any,
        stubbed_llm: list[dict[str, Any]],
        fsm_state: Any,
    ) -> None:
        await client_ai.send("/start")
        await client_ai.tap(MENU_QUIZ)
        client_ai.clear()

        await client_ai.tap("ans:0")

        assert TEST_USER_ID is not None
        assert await fsm_state["state"]() == "MyStates:waiting_for_quiz_answer"

    async def test_menu_add_word_not_swallowed_by_ai_router(
        self,
        client_ai: Any,
        ai_quiz_env: Any,
        stubbed_llm: list[dict[str, Any]],
        fsm_state: Any,
    ) -> None:
        """ai_quiz owns the bare ``add_word`` payload; the menu does not.

        Both routers register a handler for word management, so the menu's
        namespaced payload is what keeps /start usable while the AI router is
        attached. Without it the tap would be served by ai_quiz instead.
        """
        await client_ai.send("/start")
        client_ai.clear()

        await client_ai.tap(MENU_ADD_WORD)

        assert "Введите новое русское слово" in client_ai.last_text
        assert await fsm_state["state"]() == "MyStates:waiting_for_russian_word"


class TestAIQuizErrorTracking:
    """Wrong AI-quiz answers must reach the same error history ``/stat`` reads.

    The plain quiz records a miss by the Russian headword, while the AI quiz
    only ever shows English options — so the miss has to be resolved through
    the translation side, otherwise ``/stat`` and ``get_weak_words`` stay
    permanently empty for every AI-quiz mistake.
    """

    async def _add_known_word(self, session: Any) -> None:
        """Seed a word whose translation matches the stubbed option 'apple'."""
        user = await UserQueries.get_or_create_user(session, str(TEST_USER_ID))
        await WordQueries.add_user_word(session, user, "яблоко", "apple")
        await session.commit()

    async def test_wrong_answer_records_error_for_correct_option(
        self,
        client_ai: Any,
        session: Any,
        ai_quiz_env: Any,
        stubbed_llm: list[dict[str, Any]],
    ) -> None:
        """Question 1 has correct_index 0 ('apple'); tapping 1 is a miss."""
        await self._add_known_word(session)
        await client_ai.send("/quiz_ai")
        client_ai.clear()

        await client_ai.tap("aians:1:1")

        errors = await _word_errors(session)
        assert len(errors) == 1
        assert errors[0].user_word_id is not None

    async def test_wrong_answer_shows_up_in_period_statistics(
        self,
        client_ai: Any,
        session: Any,
        ai_quiz_env: Any,
        stubbed_llm: list[dict[str, Any]],
    ) -> None:
        """The regression itself: /stat reported zero errors after an AI miss."""
        await self._add_known_word(session)
        await client_ai.send("/quiz_ai")
        client_ai.clear()

        await client_ai.tap("aians:1:1")

        user = await UserQueries.get_user(session, str(TEST_USER_ID))
        assert user is not None
        stats = await WordQueries.get_errors_by_period(
            session,
            user,
            datetime.now(tz=UTC) - timedelta(days=1),
            datetime.now(tz=UTC) + timedelta(days=1),
        )

        assert stats["total_errors"] == 1
        assert [item["word"] for item in stats["by_word"]] == ["яблоко"]

    async def test_correct_answer_records_no_error(
        self,
        client_ai: Any,
        session: Any,
        ai_quiz_env: Any,
        stubbed_llm: list[dict[str, Any]],
    ) -> None:
        await self._add_known_word(session)
        await client_ai.send("/quiz_ai")
        client_ai.clear()

        # correct_index is 0 for question 1 and 1 for question 2.
        await client_ai.tap("aians:1:0")
        await client_ai.tap("aians:2:1")

        assert await _word_errors(session) == []
