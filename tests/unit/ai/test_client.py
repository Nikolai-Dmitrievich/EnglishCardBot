"""Unit tests for the AI client layer (Ollama/OpenAI wrapper)."""

from unittest.mock import MagicMock

import pytest
from httpx2 import Request
from openai import APIConnectionError, APITimeoutError

from src.ai.client import LLMUnavailableError, OllamaClient

_MESSAGES = [{"role": "user", "content": "hi"}]
_request = Request("POST", "http://localhost:11434/v1/chat/completions")


def _make_mock_client(monkeypatch: pytest.MonkeyPatch, create_fn) -> OllamaClient:
    """Helper to patch AsyncOpenAI and return an OllamaClient instance."""
    mock_openai = MagicMock()
    mock_openai.chat.completions.create = create_fn
    monkeypatch.setattr("src.ai.client.AsyncOpenAI", lambda *a, **k: mock_openai)
    return OllamaClient()


class TestOllamaClientComplete:
    """Tests for the OllamaClient.complete method."""

    async def test_returns_content_on_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify that successful responses return the message content."""

        async def mock_create(*args: object, **kwargs: object) -> object:
            mock_response = MagicMock()
            mock_response.choices = [MagicMock(message=MagicMock(content="Hello"))]
            return mock_response

        client = _make_mock_client(monkeypatch, mock_create)
        result = await client.complete(messages=_MESSAGES, timeout=10)

        assert result == "Hello"

    async def test_returns_empty_string_when_content_is_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify that a None content field returns an empty string."""

        async def mock_create(*args: object, **kwargs: object) -> object:
            mock_response = MagicMock()
            mock_response.choices = [MagicMock(message=MagicMock(content=None))]
            return mock_response

        client = _make_mock_client(monkeypatch, mock_create)
        result = await client.complete(messages=_MESSAGES, timeout=10)

        assert result == ""

    async def test_retries_on_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify that a single timeout triggers a retry and succeeds."""
        call_count = 0

        async def mock_create(*args: object, **kwargs: object) -> object:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise APITimeoutError(_request)
            mock_response = MagicMock()
            mock_response.choices = [MagicMock(message=MagicMock(content="recovered"))]
            return mock_response

        client = _make_mock_client(monkeypatch, mock_create)
        result = await client.complete(messages=_MESSAGES, timeout=10)

        assert result == "recovered"
        assert call_count == 2

    async def test_raises_unavailable_after_two_timeouts(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify that two consecutive timeouts raise LLMUnavailableError."""

        async def mock_create(*args: object, **kwargs: object) -> object:
            raise APITimeoutError(_request)

        client = _make_mock_client(monkeypatch, mock_create)
        with pytest.raises(LLMUnavailableError):
            await client.complete(messages=_MESSAGES, timeout=10)

    async def test_raises_unavailable_on_connection_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify that APIConnectionError raises LLMUnavailableError."""
        mock_request = MagicMock()

        async def mock_create(*args: object, **kwargs: object) -> object:
            raise APIConnectionError(message="connection refused", request=mock_request)

        client = _make_mock_client(monkeypatch, mock_create)
        with pytest.raises(LLMUnavailableError):
            await client.complete(messages=_MESSAGES, timeout=10)

    async def test_rethrows_unknown_exceptions(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify that unexpected exceptions are re-raised as-is."""

        async def mock_create(*args: object, **kwargs: object) -> object:
            raise ValueError("unexpected error")

        client = _make_mock_client(monkeypatch, mock_create)
        with pytest.raises(ValueError, match="unexpected error"):
            await client.complete(messages=_MESSAGES, timeout=10)


class TestLLMUnavailableError:
    """Tests for the LLMUnavailableError exception."""

    def test_is_exception(self) -> None:
        """Verify that LLMUnavailableError is a proper Exception subclass."""
        assert issubclass(LLMUnavailableError, Exception)

    def test_can_be_raised_and_caught(self) -> None:
        """Verify that the exception can be raised and caught."""

        with pytest.raises(LLMUnavailableError, match="LLM is down"):
            raise LLMUnavailableError("LLM is down")
