"""
Unit tests for the application configuration module.

This module verifies that the Pydantic V2 settings correctly parse environment
variables, handle Docker-style overrides, validate required fields, and
enforce the correct priority between process environment variables and .env files.
"""

from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.core.config import Settings

BASE_ENV = {
    "BOT__TOKEN": "42:TOKEN",
    "DB__POSTGRES_USER": "postgres",
    "DB__POSTGRES_PASSWORD": "secret",
    "DB__POSTGRES_HOST": "localhost",
    "DB__POSTGRES_PORT": "5433",
    "DB__POSTGRES_DB": "english_card_bot",
}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Callable[..., Settings]:
    """
    Provide a factory to create Settings with a clean environment.

    This fixture clears all base environment variables and sets only the
    provided keys, preventing interference from the local .env file or
    host system environment variables.
    """

    def _env(**overrides: str) -> Settings:
        # Clear all known keys to ensure a clean slate
        for key in list(BASE_ENV) + ["BOT__PROXY"]:
            monkeypatch.delenv(key, raising=False)

        # Apply base values and any overrides
        values = {**BASE_ENV, **overrides}
        for key, value in values.items():
            if value is not None:
                monkeypatch.setenv(key, str(value))
            else:
                monkeypatch.delenv(key, raising=False)

        # _env_file=None prevents pydantic-settings from reading the default .env file
        return Settings(_env_file=None)

    return _env


class TestDatabaseUrl:
    """Tests for database URL generation and security."""

    def test_builds_asyncpg_url(self, env: Callable[..., Settings]) -> None:
        """Verify that the database URL is correctly formatted for asyncpg."""
        settings = env()

        assert settings.db.db_url == (
            "postgresql+asyncpg://postgres:secret@localhost:5433/english_card_bot"
        )

    def test_password_is_not_leaked_in_repr(self, env: Callable[..., Settings]) -> None:
        """Verify that the database password is masked in string representations."""
        settings = env()

        assert "secret" not in repr(settings.db)


class TestOverrides:
    """Tests for environment variable overrides and default values."""

    def test_docker_style_host_override(self, env: Callable[..., Settings]) -> None:
        """
        Verify that Docker Compose environment variables correctly override defaults.

        This simulates the container receiving 'db:5432' instead of the
        localhost values defined in the local .env file.
        """
        settings = env(DB__POSTGRES_HOST="db", DB__POSTGRES_PORT="5432")

        assert (settings.db.postgres_host, settings.db.postgres_port) == ("db", 5432)

    def test_empty_proxy_becomes_none(self, env: Callable[..., Settings]) -> None:
        """Verify that an empty string for the proxy is parsed as None."""
        settings = env(BOT__PROXY="")

        assert settings.bot.proxy is None

    def test_proxy_is_kept(self, env: Callable[..., Settings]) -> None:
        """Verify that a valid proxy URL is correctly retained."""
        settings = env(BOT__PROXY="http://127.0.0.1:12334")

        assert settings.bot.proxy == "http://127.0.0.1:12334"

    def test_engine_defaults(self, env: Callable[..., Settings]) -> None:
        """Verify that SQLAlchemy engine settings fall back to their
        defined defaults."""
        settings = env()

        assert settings.db.engine.pool_size == 5
        assert settings.db.engine.echo is False

    def test_engine_override_from_env(self, env: Callable[..., Settings]) -> None:
        """Verify that SQLAlchemy engine settings can be overridden via
        environment variables."""
        settings = env(DB__ENGINE__POOL_SIZE="20", DB__ENGINE__ECHO="true")

        assert settings.db.engine.pool_size == 20
        assert settings.db.engine.echo is True


class TestValidation:
    """Tests for Pydantic validation rules and error handling."""

    def test_missing_token_fails(self, env: Callable[..., Settings]) -> None:
        """Verify that an empty bot token raises a ValidationError."""
        with pytest.raises(ValidationError):
            env(BOT__TOKEN="")

    def test_unknown_keys_are_ignored(self, env: Callable[..., Settings]) -> None:
        """
        Verify that unknown environment variables are ignored.

        This relies on the `extra="ignore"` setting in the Pydantic model config.
        """
        settings = env(SOME_UNUSED_VARIABLE="x")

        assert settings.bot.token.get_secret_value() == "42:TOKEN"


class TestSourcePriority:
    """Tests for the priority order of configuration sources."""

    def test_process_env_wins_over_env_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """
        Verify that process environment variables override .env file values.

        This is the core mechanism that allows Docker Compose to override
        host-specific .env values (like DB host/port) at runtime.
        """
        env_file = tmp_path / ".env"
        env_file.write_text(
            "DB__POSTGRES_HOST=localhost\nDB__POSTGRES_PORT=5433\n", encoding="utf-8"
        )

        # Simulate Docker Compose injecting these into the container's environment
        monkeypatch.setenv("DB__POSTGRES_HOST", "db")
        monkeypatch.setenv("DB__POSTGRES_PORT", "5432")

        settings = Settings(
            _env_file=env_file,
            BOT__TOKEN="42:TOKEN",
            DB__POSTGRES_USER="postgres",
            DB__POSTGRES_PASSWORD="secret",
            DB__POSTGRES_DB="english_card_bot",
        )

        assert (settings.db.postgres_host, settings.db.postgres_port) == ("db", 5432)

    def test_env_file_is_used_when_no_process_vars(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """
        Verify that the .env file is correctly parsed when no process
        environment variables are present.
        """
        for key in list(BASE_ENV) + ["BOT__PROXY"]:
            monkeypatch.delenv(key, raising=False)

        env_file = tmp_path / ".env"
        env_file.write_text(
            "BOT__TOKEN=42:TOKEN\n"
            "DB__POSTGRES_USER=postgres\n"
            "DB__POSTGRES_PASSWORD=secret\n"
            "DB__POSTGRES_DB=english_card_bot\n"
            "DB__POSTGRES_HOST=localhost\n"
            "DB__POSTGRES_PORT=5433\n",
            encoding="utf-8",
        )

        settings = Settings(_env_file=env_file)

        assert settings.db.postgres_host == "localhost"
