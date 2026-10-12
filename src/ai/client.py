import logging

from openai import APIConnectionError, APITimeoutError, AsyncOpenAI

from src.core.config import settings

logger = logging.getLogger(__name__)


class LLMUnavailableError(Exception):
    """Thrown when the LLM is unavailable (timeout, network issue)."""

    pass


class OllamaClient:
    def __init__(self):
        self.client = AsyncOpenAI(
            base_url=settings.ai.ollama_base_url, api_key="ollama"
        )
        self.model = settings.ai.ollama_model

    async def complete(self, messages: list[dict], timeout: int) -> str:
        max_attempts = 2
        for attempt in range(max_attempts):
            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    timeout=timeout,
                )
                return response.choices[0].message.content or ""
            except (APITimeoutError, APIConnectionError) as exc:
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
                    "LLM timeout/connection error, fast retry (attempt %d)", attempt + 1
                )
            except Exception as exc:
                logger.error("Unexpected LLM error: %s", type(exc).__name__)
                raise
        raise LLMUnavailableError("LLM unavailable after maximum attempts")


llm_client = OllamaClient()
