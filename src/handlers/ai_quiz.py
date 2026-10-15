"""
AI-powered quiz handler using Ollama and MCP tools.

This module provides a /quiz_ai command that generates a personalized
quiz by combining user word data (via MCP tools) with an LLM.
"""

import json
import logging
import time

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from fastmcp.client import Client
from fastmcp.client.transports.memory import FastMCPTransport
from pydantic import ValidationError

from src.ai.client import LLMUnavailableError, llm_client
from src.ai.prompts import TUTOR_PROMPT_V1
from src.ai.schemas import LLMInvalidOutputError, QuizResponse
from src.ai.utils import extract_tool_data
from src.core.config import settings
from src.mcp.tools import mcp

logger = logging.getLogger(__name__)
router = Router()


async def _send_fallback_quiz(message: Message, reason: str) -> Message:
    return await message.answer(f"{reason}. Вот обычный квиз:")


async def _send_quiz(message: Message, quiz: QuizResponse) -> Message:
    intro_text = quiz.intro
    for i, q in enumerate(quiz.questions, 1):
        if q.correct_index >= len(q.options):
            logger.warning(
                "Invalid correct_index %d for question %d (options=%d), skipping",
                q.correct_index,
                i,
                len(q.options),
            )
            continue
        intro_text += f"\n\n{i}. {q.question}\n"
        for opt in q.options:
            intro_text += f"   • {opt}\n"
        intro_text += f"Правильный ответ: {q.options[q.correct_index]}\n"
        intro_text += f"Объяснение: {q.explanation_ru}\n"

    intro_text += f"\n{quiz.summary}"
    return await message.answer(intro_text)


@router.message(Command("quiz_ai"))
async def handle_quiz_ai(message: Message) -> Message | None:
    if not settings.ai.enabled:
        return await _send_fallback_quiz(message, "AI отключен в настройках")

    if message.text is None or len(message.text) > 500:
        if message.text is None:
            return await message.answer(
                "Команда поддерживается только в текстовых сообщениях."
            )
        return await message.answer("Запрос слишком длинный (макс. 500 символов).")

    if message.from_user is None:
        return await message.answer("Не удалось определить пользователя.")

    thinking = await message.answer("Генерирую персональный квиз...")

    started = time.perf_counter()
    tools_called_log: list[dict] = []

    try:
        async with Client(FastMCPTransport(mcp)) as client:
            recent_res = await client.call_tool(
                "get_recent_words",
                {"telegram_id": str(message.from_user.id), "limit": 10},
            )
            tools_called_log.append({"name": "get_recent_words", "status": "ok"})

            weak_res = await client.call_tool(
                "get_weak_words",
                {"telegram_id": str(message.from_user.id), "limit": 5},
            )
            tools_called_log.append({"name": "get_weak_words", "status": "ok"})

        user_context: dict = {
            "recent_words": extract_tool_data(recent_res) or [],
            "weak_words": extract_tool_data(weak_res) or [],
        }

        prompt = TUTOR_PROMPT_V1.format(
            user_data=json.dumps(user_context, ensure_ascii=False, indent=2)
        )

        content = await llm_client.complete(
            messages=[
                {"role": "system", "content": prompt},
            ],
            timeout=settings.ai.request_timeout_seconds,
        )

        quiz = QuizResponse.model_validate_json(content)

        await thinking.delete()
        return await _send_quiz(message, quiz)

    except LLMUnavailableError as exc:
        logger.warning("LLM unavailable: %s", type(exc).__name__)
        return await _send_fallback_quiz(message, "AI временно недоступен")
    except (LLMInvalidOutputError, ValidationError) as exc:
        logger.warning("LLM invalid output: %s", type(exc).__name__)
        return await _send_fallback_quiz(message, "Не удалось создать AI-квиз")
    except Exception:
        logger.exception("Unexpected AI error")
        return await _send_fallback_quiz(message, "Произошла ошибка")
    finally:
        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "AI request completed in %dms. Tools: %s",
            latency_ms,
            tools_called_log,
        )
