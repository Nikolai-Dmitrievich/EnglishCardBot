# EnglishCardBot

[![Python 3.13](https://img.shields.io/badge/Python-3.13-blue.svg)](https://www.python.org/)
[![uv](https://img.shields.io/badge/Package%20Manager-uv-black.svg)](https://docs.astral.sh/uv/)
[![Aiogram](https://img.shields.io/badge/Framework-Aiogram%203.x-blue.svg)](https://docs.aiogram.dev/)
[![SQLAlchemy](https://img.shields.io/badge/ORM-SQLAlchemy%202.0-green.svg)](https://www.sqlalchemy.org/)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Type checking: MyPy](https://img.shields.io/badge/types-Mypy-blue.svg)](https://mypy.readthedocs.io/)

An asynchronous Telegram bot for interactive English vocabulary learning. Features a multiple-choice quiz, AI-powered quizzes via Ollama with post-validation, personal dictionary management, error statistics, and an MCP server for external AI agents.

## Features

- **Quiz** — randomized multiple-choice translation questions from a common and personal dictionary.
- **AI Quiz** (`/quiz_ai`) — question generation via Ollama + MCP tools, considering the user's error history. Uses `TUTOR_PROMPT_V2` with strict fill-in-the-blank validation and automatic retry (up to 2 attempts) when the model leaks the answer into the question text.
- **MCP Server** — expose bot data to external AI agents. Run with `uv run python -m src.main mcp`. Tools: `get_all_words`, `get_recent_words`, `get_user_stats`, `get_weak_words`.
- **Personal Dictionary** — add and delete words via inline buttons. The `/add` command works from any FSM state.
- **Statistics** — error reports for the last day, week, month, or a custom period.
- **FSM States** — multi-step flows without losing context.
- **Idempotent Seeding** — automatic common dictionary population without duplicates.
- **Proxy Support** — HTTP/SOCKS for bypassing Telegram API restrictions.
- **Strict Typing** — fully type-annotated code, checked by `mypy` and `ruff`.
- **Lazy Configuration** — settings are resolved on first use to avoid import-time crashes from a missing or broken `.env`.

## Tech Stack

| Category | Technologies |
| :--- | :--- |
| **Language** | Python 3.13 |
| **Package Manager** | `uv` (Astral) |
| **Telegram** | `aiogram` 3.x |
| **Database** | PostgreSQL, `SQLAlchemy` 2.0 (async), `asyncpg` |
| **Migrations** | `Alembic` |
| **Configuration** | `pydantic` V2, `pydantic-settings` |
| **AI** | Ollama (`openai`-compatible API, native `/api/chat`), `fastmcp` |
| **MCP** | `fastmcp` — stdio transport for external AI agents |
| **Testing** | `pytest`, `pytest-asyncio`, in-memory SQLite |
| **Quality** | `ruff`, `mypy`, `pre-commit` |
| **Infrastructure** | Docker, Docker Compose, GPU support (Intel/AMD/Vulkan) |

## Project Structure

```text
EnglishCardBot/
├── alembic/                 # Database migrations
├── src/
│   ├── ai/                  # Ollama client, prompts (V1/V2), response schemas
│   ├── core/                # Configuration, DB engine, middleware
│   ├── db/                  # SQLAlchemy models, repository queries
│   ├── handlers/            # Handlers: common, quiz, ai_quiz, stat, words
│   ├── mcp/                 # MCP tools for AI quiz & external agents
│   ├── states/              # FSM states
│   ├── utils/               # Validators and utilities
│   └── main.py              # Entry point (bot + MCP server)
├── tests/                   # Unit and integration tests
├── docker-compose.yml       # Service orchestration (bot + db + ollama)
├── Dockerfile               # Multi-stage build
├── pyproject.toml           # Dependencies and tool configs
└── uv.lock                  # Deterministic dependency resolution
```

## Quick Start

### 1. Prerequisites
- Python 3.13+
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Docker & Docker Compose

### 2. Installation
```bash
git clone https://github.com/Nikolai-Dmitrievich/EnglishCardBot.git
cd EnglishCardBot
uv sync
```

### 3. Environment Setup
```bash
cp .env.example .env
# Open .env and add your token from @BotFather
```

### 4. Start the Database
```bash
docker compose up db -d
```

### 5. Start the Bot
```bash
uv run python -m src.main
```

## Full Docker Deployment

Runs the entire stack: bot + PostgreSQL + Ollama (with auto model download).

```bash
docker compose up --build -d
docker compose logs -f bot
```

> **Ollama port:** In Docker, Ollama is exposed on port `11435` (mapped to `11434` inside the container) to avoid conflicts with a host-local Ollama instance.
>
> **GPU support:** The Ollama container includes device mappings for Intel/AMD GPUs (`/dev/dri`, `/dev/kfd`) and Vulkan. Set `OLLAMA_VULKAN=1` for GPU-accelerated inference.
>
> **Proxy in Docker:** `127.0.0.1` inside a container refers to the container itself. Use `host.docker.internal` (macOS/Windows) or the host's LAN IP (Linux), or run the bot on the host machine (see "Quick Start").

## Bot Commands

| Command | Description |
| :--- | :--- |
| `/start` | Main menu with actions |
| `/quiz` | Start a quiz |
| `/quiz_ai` | AI-powered quiz based on your words |
| `/stat` | Error statistics for a period |
| `/add` | Add a word to your personal dictionary (works from any state) |
| `/stop` | Stop quiz / reset state |
| `/help` | Command reference |

## MCP Server

The bot can also run as an MCP server, exposing bot data to external AI agents (e.g., Claude Desktop, Cursor):

```bash
uv run python -m src.main mcp
```

### Available Tools

| Tool | Description |
| :--- | :--- |
| `get_all_words` | Get a sample of common dictionary words (1–50) |
| `get_recent_words` | Get recently added words for a user (1–20) |
| `get_user_stats` | Get user's custom word count and dictionary size |
| `get_weak_words` | Get words the user struggles with, sorted by error count (1–20) |

## AI Quiz — Validation & Retry

The AI quiz uses a two-stage validation pipeline:

1. **Pydantic validation** — ensures the JSON structure matches `QuizResponse` / `QuizQuestion` schemas.
2. **Fill-in-the-blank check** — verifies that every question contains `____` and that the correct answer does not appear anywhere in the question text.

If validation fails, the prompt is retried once (up to 2 attempts) with an additional fix note. If both attempts fail, the user sees a fallback message suggesting they try again later.

## Tests & Quality

```bash
# Run all tests
uv run pytest

# Linter and formatter
uv run ruff check --fix .
uv run ruff format .

# Type checking
uv run mypy src/

# Pre-commit hooks
uv run pre-commit install
uv run pre-commit run --all-files
```

## Environment Variables

| Variable | Description | Default |
| :--- | :--- | :--- |
| `BOT__TOKEN` | Telegram bot token *(required)* | `your_bot_token_here` |
| `BOT__PROXY` | Proxy URL for Telegram API | `""` |
| `DB__POSTGRES_USER` | Database username | `postgres` |
| `DB__POSTGRES_PASSWORD` | Database password | `postgres` |
| `DB__POSTGRES_HOST` | Database host | `localhost` (host), `db` (Docker) |
| `DB__POSTGRES_PORT` | Database port | `5433` (host), `5432` (Docker) |
| `DB__POSTGRES_DB` | Database name | `english_card_bot` |
| `AI__ENABLED` | Enable AI quiz | `true` |
| `AI__OLLAMA_BASE_URL` | Ollama URL | `http://localhost:11434` |
| `AI__OLLAMA_MODEL` | Ollama model | `qwen2.5:7b` |
| `AI__REQUEST_TIMEOUT_SECONDS` | AI request timeout | `60` |
| `AI__PROMPT_VERSION` | Prompt template version | `tutor_v2` |
| `AI__NUM_CTX` | Context window size | `4096` |

Full list with SQLAlchemy settings — see `.env.example`.
