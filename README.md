# EnglishCardBot

[![Python 3.13](https://img.shields.io/badge/Python-3.13-blue.svg)](https://www.python.org/)
[![uv](https://img.shields.io/badge/Package%20Manager-uv-black.svg)](https://docs.astral.sh/uv/)
[![Aiogram](https://img.shields.io/badge/Framework-Aiogram%203.x-blue.svg)](https://docs.aiogram.dev/)
[![SQLAlchemy](https://img.shields.io/badge/ORM-SQLAlchemy%202.0-green.svg)](https://www.sqlalchemy.org/)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Type checking: MyPy](https://img.shields.io/badge/types-Mypy-blue.svg)](https://mypy.readthedocs.io/)

An asynchronous Telegram bot for interactive English vocabulary learning. Features a multiple-choice quiz, AI-powered quizzes via Ollama, personal dictionary management, and error statistics.

## Features

- **Quiz** — randomized multiple-choice translation questions from a common and personal dictionary.
- **AI Quiz** (`/quiz_ai`) — question generation via Ollama + MCP tools, considering the user's error history.
- **Personal Dictionary** — add and delete words via inline buttons.
- **Statistics** — error reports for the last day, week, month, or a custom period.
- **FSM States** — multi-step flows without losing context.
- **Idempotent Seeding** — automatic common dictionary population without duplicates.
- **Proxy Support** — HTTP/SOCKS for bypassing Telegram API restrictions.
- **Strict Typing** — 100% type-annotated code, checked by `mypy` and `ruff`.

## Tech Stack

| Category | Technologies |
| :--- | :--- |
| **Language** | Python 3.13 |
| **Package Manager** | `uv` (Astral) |
| **Telegram** | `aiogram` 3.x |
| **Database** | PostgreSQL, `SQLAlchemy` 2.0 (async), `asyncpg` |
| **Migrations** | `Alembic` |
| **Configuration** | `pydantic` V2, `pydantic-settings` |
| **AI** | Ollama (`openai`-compatible API), `fastmcp` |
| **Testing** | `pytest`, `pytest-asyncio`, in-memory SQLite |
| **Quality** | `ruff`, `mypy`, `pre-commit` |
| **Infrastructure** | Docker, Docker Compose |

## Project Structure

```text
EnglishCardBot/
├── alembic/                 # Database migrations
├── src/
│   ├── ai/                  # Ollama client, prompts, response schemas
│   ├── core/                # Configuration, DB engine, middleware
│   ├── db/                  # SQLAlchemy models, repository queries
│   ├── handlers/            # Handlers: common, quiz, ai_quiz, stat, words
│   ├── mcp/                 # MCP tools for AI quiz
│   ├── states/              # FSM states
│   ├── utils/               # Validators and utilities
│   └── main.py              # Entry point
├── tests/                   # Unit and integration tests
├── docker-compose.yml       # Service orchestration
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

> **Proxy in Docker:** `127.0.0.1` inside a container refers to the container itself. Use `host.docker.internal` (macOS/Windows) or the host's LAN IP (Linux), or run the bot on the host machine (see "Quick Start").

## Bot Commands

| Command | Description |
| :--- | :--- |
| `/start` | Main menu with actions |
| `/quiz` | Start a quiz |
| `/quiz_ai` | AI-powered quiz based on your words |
| `/stat` | Error statistics for a period |
| `/stop` | Stop quiz / reset state |
| `/help` | Command reference |

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
| `AI__OLLAMA_MODEL` | Ollama model | `your_ai_model` |
| `AI__REQUEST_TIMEOUT_SECONDS` | AI request timeout | `60` |

Full list with SQLAlchemy settings — see `.env.example`.
