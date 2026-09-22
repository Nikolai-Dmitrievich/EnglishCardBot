"""
Unit tests for the input validation utilities.

This module verifies that the `is_russian` and `is_english` functions
correctly validate strings containing only the respective alphabets,
spaces, and hyphens, while rejecting numbers, punctuation, and mixed scripts.
"""

import pytest

from src.utils.validators import is_english, is_russian


class TestIsRussian:
    """Tests for the `is_russian` validation function."""

    @pytest.mark.parametrize(
        "text",
        [
            "здравствуйте",
            "Екатеринбург",
            "рука-лицо",
            "запасной аэродром",
            "ёжик",
            "ЁЛКА",
        ],
    )
    def test_accepts_russian(self, text: str) -> None:
        """Verify that valid Russian strings, including 'ё' and hyphens,
        are accepted."""
        assert is_russian(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "python",
            "164",
            "привет2",
            "привет!",
            "",
            "   ",
        ],
    )
    def test_rejects_non_russian(self, text: str) -> None:
        """
        Verify that strings containing Latin characters, numbers,
        punctuation, or consisting only of whitespace are rejected.
        """
        assert is_russian(text) is False

    def test_strips_surrounding_spaces(self) -> None:
        """Verify that surrounding whitespace is ignored during validation."""
        assert is_russian("  слово  ") is True


class TestIsEnglish:
    """Tests for the `is_english` validation function."""

    @pytest.mark.parametrize(
        "text",
        [
            "hi",
            "python",
            "Hello-World",
            "run time",
            "Debug",
            "A",
        ],
    )
    def test_accepts_english(self, text: str) -> None:
        """Verify that valid English strings, including hyphens and spaces,
        are accepted."""
        assert is_english(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "досвидания",
            "8765",
            "to be?",
            "",
            "  \t ",
        ],
    )
    def test_rejects_non_english(self, text: str) -> None:
        """
        Verify that strings containing Cyrillic characters, numbers,
        punctuation, or consisting only of whitespace are rejected.
        """
        assert is_english(text) is False

    def test_rejects_mixed_alphabets(self) -> None:
        """Verify that strings containing a mix of Cyrillic and Latin
        characters are rejected."""
        assert is_english("привет world") is False
