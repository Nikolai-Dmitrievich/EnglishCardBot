from typing import Self

from pydantic import BaseModel, Field, model_validator


class QuizQuestion(BaseModel):
    question: str = Field(description="Question in English")
    options: list[str] = Field(
        min_length=2,
        max_length=4,
        description="List of 2 to 4 possible answers in English",
    )
    correct_index: int = Field(
        ge=0, description="Zero-based index of the correct answer in the 'options' list"
    )
    explanation_ru: str = Field(
        description="Detailed explanation of the correct answer in Russian"
    )

    @model_validator(mode="after")
    def check_correct_index(self) -> Self:
        """Ensures that correct_index does not go beyond
        the bounds of the options list."""
        if self.correct_index >= len(self.options):
            raise ValueError(
                f"correct_index ({self.correct_index}) is out of bounds "
                f"for options list of length {len(self.options)}"
            )
        return self


class QuizResponse(BaseModel):
    intro: str = Field(description="A brief, friendly introduction in Russian")
    questions: list[QuizQuestion] = Field(
        min_length=1, max_length=5, description="A list of 1 to 5 quiz questions"
    )
    summary: str = Field(
        description="A brief encouraging summary or call to action in Russian"
    )


class LLMInvalidOutputError(Exception):
    """Thrown if the LLM returned JSON but it failed business validation."""

    pass
