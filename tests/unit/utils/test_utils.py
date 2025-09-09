import pytest
from src.bot import is_russian, is_english


@pytest.mark.parametrize("text,expected", [
    ("здравствуйте", True),
    ("Екатеринбург", True),
    ("рука-лицо", True),
    ("python", False),
    ("164", False),
    ("", False),
])
def test_is_russian(text, expected):
    """
    Тестирует функцию is_russian на корректное определение русскоязычных строк.

    Args:
        text (str): Входная строка для проверки.
        expected (bool): Ожидаемый результат (True для строк на русском языке, False - иначе).
    """
    assert is_russian(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("hi", True),
    ("python", True),
    ("Hello-World", True),
    ("досвидания", False),
    ("8765", False),
    ("", False),
])
def test_is_english(text, expected):
    """
    Тестирует функцию is_english на корректное определение англоязычных строк.

    Args:
        text (str): Входная строка для проверки.
        expected (bool): Ожидаемый результат (True для строк на английском языке, False - иначе).
    """
    assert is_english(text) == expected
