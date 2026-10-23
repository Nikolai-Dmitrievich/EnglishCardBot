import logging

import httpx

from src.core.config import settings

logger = logging.getLogger(__name__)


class LLMUnavailableError(Exception):
    """Thrown when the LLM is unavailable (timeout, network issue)."""

    pass


class _EmptyResponseError(Exception):
    """Internal sentinel: model answered 200 OK but with no content.

    Kept separate from ``ValueError`` so unrelated programming errors are
    never mistaken for an empty completion and silently retried.
    """

    pass


class OllamaClient:
    """Native Ollama client using /api/chat endpoint.

    This endpoint supports `think: false` to disable reasoning for
    reasoning models (qwen3, deepseek-r1, etc.), avoiding the situation
    where the entire `num_predict` budget is consumed by thoughts.
    """

    def __init__(self):
        self._base_url = settings.ai.ollama_base_url.rstrip("/")
        self._model = settings.ai.ollama_model
        self._http = httpx.AsyncClient(trust_env=False, timeout=30)

    async def close(self) -> None:
        await self._http.aclose()

    async def complete(
        self,
        messages: list[dict],
        timeout: int,
        format_schema: dict | None = None,
    ) -> str:
        """Send a chat request to Ollama's native /api/chat endpoint.

        Uses `think: false` to disable reasoning output, and optionally
        `format` with a JSON schema so the model returns a single
        well-formed JSON object on the first attempt.
        """
        max_attempts = 2
        for attempt in range(max_attempts):
            try:
                payload: dict = {
                    "model": self._model,
                    "messages": messages,
                    "stream": False,
                    "think": False,
                    "options": {"num_ctx": settings.ai.num_ctx},
                }
                if format_schema is not None:
                    payload["format"] = format_schema

                resp = await self._http.post(
                    f"{self._base_url}/api/chat",
                    json=payload,
                    timeout=timeout,
                )
                resp.raise_for_status()

                data = resp.json()
                content = data.get("message", {}).get("content", "") or ""

                if not content.strip():
                    raise _EmptyResponseError("empty response")

                return content

            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                if attempt == max_attempts - 1:
                    logger.warning(
                        "LLM unavailable after %d attempts: %s",
                        max_attempts,
                        type(exc).__name__,
                    )
                    raise LLMUnavailableError(
                        f"LLM network error: {type(exc).__name__}"
                    ) from exc
                logger.info(
                    "LLM timeout/connection error, fast retry (attempt %d)",
                    attempt + 1,
                )
            except _EmptyResponseError:
                # Empty content — retry once
                if attempt == max_attempts - 1:
                    # ``from None``: the sentinel carries no extra context, so
                    # the chain would only bury the real reason (empty output).
                    raise LLMUnavailableError("LLM returned empty response") from None
                logger.info(
                    "LLM returned empty content, retrying (attempt %d)",
                    attempt + 1,
                )
            except Exception as exc:
                logger.error("Unexpected LLM error: %s", type(exc).__name__)
                raise

        raise LLMUnavailableError("LLM unavailable after maximum attempts")

    async def complete_with_format(
        self,
        messages: list[dict],
        format_schema: dict,
        timeout: int,
    ) -> str:
        """Convenience wrapper that always passes a JSON schema format."""
        return await self.complete(messages, timeout, format_schema=format_schema)


llm_client = OllamaClient()
