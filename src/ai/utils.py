# src/ai/utils.py
import re
from typing import Any, Protocol

from pydantic import BaseModel

from src.ai.schemas import QuizResponse


class MCPToolResult(Protocol):
    data: Any


def extract_tool_data(result: MCPToolResult) -> Any:
    if result is None:
        return None
    data = result.data

    if isinstance(data, BaseModel):
        return data.model_dump(mode="json")

    if isinstance(data, list):
        return [
            item.model_dump(mode="json") if isinstance(item, BaseModel) else item
            for item in data
        ]

    return data


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def validate_quiz(quiz: QuizResponse) -> list[str]:
    """Return a list of validation errors for *quiz*.

    A question is invalid when the correct option appears in the question
    text (case-insensitive, whole-word match).  Questions that contain the
    blank ``____`` are considered valid.
    """
    errors: list[str] = []

    for idx, q in enumerate(quiz.questions):
        correct = q.options[q.correct_index]
        # Case-insensitive whole-word check — this is the primary rule.
        pattern = re.compile(rf"\b{re.escape(correct)}\b", re.IGNORECASE)
        if pattern.search(q.question):
            errors.append(
                f"Question {idx + 1}: correct option '{correct}' appears in question"
            )
            continue

        # Check that the blank is present (only if correct word is not leaked).
        if "____" not in q.question:
            errors.append(f"Question {idx + 1}: missing blank ____")

    return errors
