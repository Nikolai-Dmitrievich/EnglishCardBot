"""
Input validation utilities for the English Card Bot.

This module provides functions for validating that user input contains
only characters from a specific language alphabet, along with spaces
and hyphens.
"""

import re


def is_russian(text: str) -> bool:
    """
    Check whether the text contains only Russian letters, spaces, or hyphens.

    Args:
        text: The input string to validate.

    Returns:
        True if the text consists exclusively of Russian characters,
        spaces, and hyphens; False otherwise.
    """
    return bool(re.fullmatch(r"[а-яА-ЯёЁ\s-]+", text.strip()))


def is_english(text: str) -> bool:
    """
    Check whether the text contains only English letters, spaces, or hyphens.

    Args:
        text: The input string to validate.

    Returns:
        True if the text consists exclusively of English characters,
        spaces, and hyphens; False otherwise.
    """
    return bool(re.fullmatch(r"[a-zA-Z\s-]+", text.strip()))
