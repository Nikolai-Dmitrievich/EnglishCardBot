"""Unit tests for the AI client layer (native Ollama /api/chat)."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.ai.client import LLMUnavailableError, OllamaClient

_MESSAGES = [{"role": "user", "content": "hi"}]


def _make_client(monkeypatch: pytest.MonkeyPatch, mock_post: AsyncMock) -> OllamaClient:
    """Helper to patch httpx.AsyncClient.post and return an OllamaClient."""
    mock_http = MagicMock()
    mock_http.post = mock_post
    mock_http.aclose = AsyncMock()
    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **k: mock_http)
    return OllamaClient()


class TestOllamaClientComplete:
    """Tests for the OllamaClient.complete method."""

    async def test_returns_content_on_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify that successful responses return the message content."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "message": {"content": "Hello"},
            "done": True,
        }
        mock_response.raise_for_status = MagicMock()

        mock_post = AsyncMock(return_value=mock_response)
        client = _make_client(monkeypatch, mock_post)
        result = await client.complete(messages=_MESSAGES, timeout=10)

        assert result == "Hello"
        # Verify think: false is always sent
        call_args = mock_post.call_args
        assert call_args.kwargs["json"]["think"] is False

    async def test_raises_unavailable_when_content_is_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify that a None content field triggers a retry and fails."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"message": {}, "done": True}
        mock_response.raise_for_status = MagicMock()

        mock_post = AsyncMock(return_value=mock_response)
        client = _make_client(monkeypatch, mock_post)
        with pytest.raises(LLMUnavailableError, match="empty response"):
            await client.complete(messages=_MESSAGES, timeout=10)

    async def test_retries_on_empty_content(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify that empty content triggers a retry and succeeds."""
        call_count = 0

        def _post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            if call_count == 1:
                mock_response.json.return_value = {"message": {}, "done": True}
            else:
                mock_response.json.return_value = {
                    "message": {"content": "recovered"},
                    "done": True,
                }
            return mock_response

        client = _make_client(monkeypatch, AsyncMock(side_effect=_post))
        result = await client.complete(messages=_MESSAGES, timeout=10)

        assert result == "recovered"
        assert call_count == 2

    async def test_raises_unavailable_after_two_empty_responses(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify that two consecutive empty responses raise LLMUnavailableError."""

        def _post(*args, **kwargs):
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.json.return_value = {"message": {}, "done": True}
            return mock_response

        client = _make_client(monkeypatch, AsyncMock(side_effect=_post))
        with pytest.raises(LLMUnavailableError, match="empty response"):
            await client.complete(messages=_MESSAGES, timeout=10)

    async def test_raises_unavailable_on_timeout(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify that httpx.TimeoutError raises LLMUnavailableError."""
        mock_post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        client = _make_client(monkeypatch, mock_post)
        with pytest.raises(LLMUnavailableError, match="network error"):
            await client.complete(messages=_MESSAGES, timeout=10)

    async def test_raises_unavailable_on_connection_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify that httpx.ConnectError raises LLMUnavailableError."""
        mock_post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        client = _make_client(monkeypatch, mock_post)
        with pytest.raises(LLMUnavailableError, match="network error"):
            await client.complete(messages=_MESSAGES, timeout=10)

    async def test_retries_on_timeout_then_succeeds(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify that a single timeout triggers a retry and succeeds."""
        call_count = 0

        def _post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.TimeoutException("timeout")
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.json.return_value = {
                "message": {"content": "recovered"},
                "done": True,
            }
            return mock_response

        client = _make_client(monkeypatch, AsyncMock(side_effect=_post))
        result = await client.complete(messages=_MESSAGES, timeout=10)

        assert result == "recovered"
        assert call_count == 2

    async def test_rethrows_unknown_exceptions(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify that unexpected exceptions are re-raised as-is."""

        def _post(*args, **kwargs):
            raise ValueError("unexpected error")

        client = _make_client(monkeypatch, AsyncMock(side_effect=_post))
        with pytest.raises(ValueError, match="unexpected error"):
            await client.complete(messages=_MESSAGES, timeout=10)

    async def test_format_schema_is_passed_when_provided(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify that format_schema is included in the request payload."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "message": {"content": '{"intro":"hi","questions":[],"summary":"bye"}'},
            "done": True,
        }
        mock_response.raise_for_status = MagicMock()

        mock_post = AsyncMock(return_value=mock_response)
        client = _make_client(monkeypatch, mock_post)
        schema = {"type": "object", "properties": {"intro": {"type": "string"}}}
        await client.complete(messages=_MESSAGES, timeout=10, format_schema=schema)

        call_args = mock_post.call_args
        assert call_args.kwargs["json"]["format"] == schema

    async def test_complete_with_format_delegates_to_complete(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify that complete_with_format passes the schema correctly."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "message": {"content": '{"intro":"hi","questions":[],"summary":"bye"}'},
            "done": True,
        }
        mock_response.raise_for_status = MagicMock()

        mock_post = AsyncMock(return_value=mock_response)
        client = _make_client(monkeypatch, mock_post)
        schema = {"type": "object"}
        await client.complete_with_format(
            messages=_MESSAGES, format_schema=schema, timeout=10
        )

        call_args = mock_post.call_args
        assert call_args.kwargs["json"]["format"] == schema

    async def test_num_ctx_is_set_from_config(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify that num_ctx is included in the request options."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "message": {"content": "test"},
            "done": True,
        }
        mock_response.raise_for_status = MagicMock()

        mock_post = AsyncMock(return_value=mock_response)
        client = _make_client(monkeypatch, mock_post)
        await client.complete(messages=_MESSAGES, timeout=10)

        call_args = mock_post.call_args
        assert call_args.kwargs["json"]["options"]["num_ctx"] == 4096


class TestLLMUnavailableError:
    """Tests for the LLMUnavailableError exception."""

    def test_is_exception(self) -> None:
        """Verify that LLMUnavailableError is a proper Exception subclass."""
        assert issubclass(LLMUnavailableError, Exception)

    def test_can_be_raised_and_caught(self) -> None:
        """Verify that the exception can be raised and caught."""
        with pytest.raises(LLMUnavailableError, match="LLM is down"):
            raise LLMUnavailableError("LLM is down")
