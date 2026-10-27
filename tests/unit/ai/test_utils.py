"""Unit tests for the AI quiz post-validation utility."""

from src.ai.schemas import QuizQuestion, QuizResponse
from src.ai.utils import validate_quiz


class TestValidateQuiz:
    """Tests for the validate_quiz function."""

    def _make_quiz(self, questions: list[dict]) -> QuizResponse:
        return QuizResponse(
            intro="intro",
            questions=[QuizQuestion(**q) for q in questions],
            summary="summary",
        )

    def test_passes_when_all_questions_have_blank(self) -> None:
        quiz = self._make_quiz(
            [
                {
                    "question": "I need a new ____ to write notes.",
                    "options": ["notebook", "spoon", "guitar", "cloud"],
                    "correct_index": 0,
                    "explanation_ru": "Пояснение",
                },
            ]
        )
        assert validate_quiz(quiz) == []

    def test_fails_when_correct_option_in_question(self) -> None:
        quiz = self._make_quiz(
            [
                {
                    "question": "The network connection was lost.",
                    "options": ["connection", "network", "server", "client"],
                    "correct_index": 0,
                    "explanation_ru": "Пояснение",
                },
            ]
        )
        errors = validate_quiz(quiz)
        assert len(errors) == 1
        assert "connection" in errors[0]

    def test_fails_when_blank_missing(self) -> None:
        quiz = self._make_quiz(
            [
                {
                    "question": "I need a new spoon to write notes.",
                    "options": ["notebook", "spoon", "guitar", "cloud"],
                    "correct_index": 0,
                    "explanation_ru": "Пояснение",
                },
            ]
        )
        errors = validate_quiz(quiz)
        assert len(errors) == 1
        assert "missing blank" in errors[0]

    def test_fails_both_blank_missing_and_correct_in_text(self) -> None:
        """When the correct word is spelled out AND there's no blank,
        only the correct-in-text error is reported (it's checked first)."""
        quiz = self._make_quiz(
            [
                {
                    "question": "I need a new notebook to write notes.",
                    "options": ["notebook", "spoon", "guitar", "cloud"],
                    "correct_index": 0,
                    "explanation_ru": "Пояснение",
                },
            ]
        )
        errors = validate_quiz(quiz)
        # correct-in-text check runs first
        assert len(errors) == 1
        assert "notebook" in errors[0]

    def test_case_insensitive_match(self) -> None:
        quiz = self._make_quiz(
            [
                {
                    "question": "I need a new Notebook to write notes.",
                    "options": ["notebook", "spoon", "guitar", "cloud"],
                    "correct_index": 0,
                    "explanation_ru": "Пояснение",
                },
            ]
        )
        errors = validate_quiz(quiz)
        assert len(errors) == 1
        assert "notebook" in errors[0]

    def test_whole_word_only(self) -> None:
        """'notebook' must not be flagged as the answer 'note' — the match
        uses word boundaries, so a longer word containing it is allowed."""
        quiz = self._make_quiz(
            [
                {
                    "question": "I need a new notebook to write my ____.",
                    "options": ["note", "spoon", "guitar", "cloud"],
                    "correct_index": 0,
                    "explanation_ru": "Пояснение",
                },
            ]
        )
        assert validate_quiz(quiz) == []

    def test_whole_word_match_inside_sentence(self) -> None:
        """The answer hidden mid-sentence is still a leak."""
        quiz = self._make_quiz(
            [
                {
                    "question": "The ____ was lost, so the client disconnected.",
                    "options": ["client", "spoon", "guitar", "cloud"],
                    "correct_index": 0,
                    "explanation_ru": "Пояснение",
                },
            ]
        )
        errors = validate_quiz(quiz)
        assert len(errors) == 1
        assert "client" in errors[0]

    def test_multiple_questions_all_pass(self) -> None:
        quiz = self._make_quiz(
            [
                {
                    "question": "I need a new ____ to write notes.",
                    "options": ["notebook", "spoon", "guitar", "cloud"],
                    "correct_index": 0,
                    "explanation_ru": "Пояснение",
                },
                {
                    "question": "The ____ was lost in the cloud.",
                    "options": ["connection", "spoon", "guitar", "cloud"],
                    "correct_index": 0,
                    "explanation_ru": "Пояснение",
                },
            ]
        )
        assert validate_quiz(quiz) == []

    def test_multiple_questions_some_fail(self) -> None:
        quiz = self._make_quiz(
            [
                {
                    "question": "I need a new ____ to write notes.",
                    "options": ["notebook", "spoon", "guitar", "cloud"],
                    "correct_index": 0,
                    "explanation_ru": "Пояснение",
                },
                {
                    "question": "The connection was lost.",
                    "options": ["connection", "spoon", "guitar", "cloud"],
                    "correct_index": 0,
                    "explanation_ru": "Пояснение",
                },
            ]
        )
        errors = validate_quiz(quiz)
        assert len(errors) == 1
        assert "Question 2" in errors[0]

    def test_correct_option_not_in_question(self) -> None:
        """Only the correct option is checked — a distractor sitting in the
        sentence is not a leak and must not fail validation."""
        quiz = self._make_quiz(
            [
                {
                    "question": "Next to the spoon I put a ____ for my notes.",
                    "options": ["notebook", "spoon", "guitar", "cloud"],
                    "correct_index": 0,
                    "explanation_ru": "Пояснение",
                },
            ]
        )
        # 'notebook' is the correct option and it's NOT in the question text
        assert validate_quiz(quiz) == []
