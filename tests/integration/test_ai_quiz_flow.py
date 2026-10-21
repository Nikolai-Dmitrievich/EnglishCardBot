"""Integration tests for the AI quiz flow.

The LLM completion and the MCP session factory are stubbed, so the tests
exercise the real router stack (ai_quiz + quiz + words) without touching
Ollama or PostgreSQL.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from src.db.models import WordError
from src.db.queries import UserQueries, WordQueries
from tests.conftest import TEST_USER_ID

AI_QUIZ_STATE = "MyStates:waiting_for_ai_quiz_answer"


async def _word_errors(session: Any) -> list[WordError]:
    """Fetch every recorded error row, ignoring the period filters."""
    result = await session.execute(select(WordError))
    return list(result.scalars().all())


def _quiz_payload(question_count: int = 2) -> str:
    """Build a QuizResponse-shaped JSON payload for the stubbed LLM."""
    questions = [
        {
            "question": f"What is the English word for 'слово{i}'?",
            "options": ["apple", "banana", "cherry", "date"],
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
def ai_quiz_env(
    session_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
    common_words: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Point the MCP tools at the in-memory test database."""
    monkeypatch.setattr("src.mcp.tools.session_factory", session_factory)
    return common_words


class TestAIQuizGeneration:
    """The /quiz_ai command must build a quiz from every available word source."""

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
        client_ai.clear()

        await client_ai.tap("ans:0")

        assert TEST_USER_ID is not None
        assert await fsm_state["state"]() == "MyStates:waiting_for_quiz_answer"


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
