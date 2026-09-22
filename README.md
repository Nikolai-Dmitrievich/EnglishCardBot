# EnglishCardBot

[![Python 3.13](https://img.shields.io/badge/Python-3.13-blue.svg)](https://www.python.org/)
[![uv](https://img.shields.io/badge/Package%20Manager-uv-black.svg)](https://docs.astral.sh/uv/)
[![Aiogram](https://img.shields.io/badge/Framework-Aiogram%203.x-blue.svg)](https://docs.aiogram.dev/)
[![SQLAlchemy](https://img.shields.io/badge/ORM-SQLAlchemy%202.0-green.svg)](https://www.sqlalchemy.org/)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Type checking: MyPy](https://img.shields.io/badge/types-Mypy-blue.svg)](https://mypy.readthedocs.io/)

An asynchronous, production-ready Telegram bot designed for interactive English vocabulary learning. It features a quiz system, custom dictionary management, and robust state handling, built with modern Python best practices.

## Features

- **Interactive Quiz**: Randomized multiple-choice questions drawn from both a common dictionary and the user's custom words.
- **Custom Dictionary**: Users can add, view, and delete their own word-translation pairs.
- **Robust State Management**: Utilizes Aiogram's FSM to guide users through multi-step flows (e.g., adding a word) without losing context.
- **Idempotent Seeding**: Automatically populates the initial common dictionary on startup without creating duplicates.
- **Proxy Support**: Built-in support for HTTP/SOCKS proxies, essential for regions with restricted Telegram API access.
- **Strict Typing & Linting**: 100% type-annotated codebase enforced by `mypy` and `ruff`, with pre-commit hooks.

## Tech Stack

| Category | Technologies |
| :--- | :--- |
| **Language** | Python 3.13 |
| **Package Manager** | `uv` (Astral) |
| **Telegram Framework** | `aiogram` 3.x |
| **Database & ORM** | PostgreSQL, `SQLAlchemy` 2.0 (async), `asyncpg` |
| **Migrations** | `Alembic` |
| **Configuration** | `pydantic` V2, `pydantic-settings` |
| **Testing** | `pytest`, `pytest-asyncio`, in-memory SQLite |
| **Quality Assurance** | `ruff` (linting/formatting), `mypy`, `pre-commit` |
| **Infrastructure** | Docker, Docker Compose (multi-stage builds) |

## Project Structure

The project follows the standard `src` layout for clean separation of concerns:

```text
EnglishCardBot/
├── alembic/                 # Database migration scripts
├── src/
│   ├── core/                # Core configurations (Pydantic settings, DB engine, middlewares)
│   ├── db/                  # Database layer (SQLAlchemy models, repository queries)
│   ├── handlers/            # Aiogram routers (common, quiz, words)
│   ├── states/              # FSM state definitions
│   ├── utils/               # Helper functions (e.g., input validators)
│   └── main.py              # Application entry point
├── tests/                   # Unit and integration tests
├── docker-compose.yml       # Local development & production orchestration
├── Dockerfile               # Multi-stage production build
├── pyproject.toml           # Project metadata, dependencies, and tool configs
└── uv.lock                  # Deterministic dependency resolution
```

## Quick Start (Local Development)

We recommend the **"Containerized Dependencies"** pattern for local development. This keeps the database isolated in Docker while running the Python code on the host machine, allowing seamless integration with local proxy tools (like Hiddify).

### 1. Prerequisites
- Python 3.13+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) package manager
- Docker & Docker Compose

### 2. Installation
```bash
# Clone the repository
git clone https://github.com/Nikolai-Dmitrievich/EnglishCardBot.git
cd EnglishCardBot

# Install project dependencies (including dev tools)
uv sync
```

### 3. Environment Configuration
```bash
# Copy the example environment file
cp .env.example .env

# Edit .env and add your Telegram Bot Token from @BotFather
# Leave BOT__PROXY empty for direct connection, or set it to your local proxy (e.g., http://127.0.0.1:12334)
```

### 4. Run the Infrastructure
Start only the PostgreSQL database in the background:
```bash
docker compose up db -d
```
*(Note: The `docker-compose.yml` automatically exposes the DB on `localhost:5433` for local host access).*

### 5. Run the Bot
```bash
# Apply migrations and start the bot locally
uv run python -m src.main
```

## Production Deployment (Full Docker)

To run the entire stack (Bot + Database) inside Docker:

```bash
# Build and start all services
docker compose up --build -d

# View logs
docker compose logs -f bot
```
> **Proxy Note for Docker**: If you are using a local proxy on your host machine, `127.0.0.1` will **not** work inside the container. You must either:
> 1. Use `host.docker.internal` (Windows/macOS) or the Docker gateway IP (Linux) in your `.env`.
> 2. Configure your proxy software to listen on `0.0.0.0` and use the host's LAN IP.
> 3. Run the bot on the host machine (as described in the Quick Start section).

## Testing & Quality Assurance

The project is equipped with comprehensive automated checks.

```bash
# Run all tests
uv run pytest

# Run linter and auto-fixer
uv run ruff check --fix .
uv run ruff format .

# Run static type checker
uv run mypy src/

# Install pre-commit hooks (runs automatically on `git commit`)
uv run pre-commit install
uv run pre-commit run --all-files
```

## Environment Variables

| Variable | Description | Default (Local) | Default (Docker) |
| :--- | :--- | :--- | :--- |
| `BOT__TOKEN` | Telegram Bot API Token *(Required)* | `your_bot_token_here` | N/A |
| `BOT__PROXY` | Proxy URL for Telegram API | `""` (empty) | `""` |
| `DB__POSTGRES_USER` | Database username | `postgres` | `postgres` |
| `DB__POSTGRES_PASSWORD`| Database password | `postgres` | `postgres` |
| `DB__POSTGRES_HOST` | Database host | `localhost` | `db` |
| `DB__POSTGRES_PORT` | Database port | `5433` | `5432` |
| `DB__POSTGRES_DB` | Database name | `english_card_bot` | `english_card_bot` |

*See `.env.example` for advanced SQLAlchemy engine and session configurations.*
