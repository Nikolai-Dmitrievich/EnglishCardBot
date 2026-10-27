"""
Application configuration management.

This module defines the hierarchical configuration structure using Pydantic V2,
providing type validation, environment variable parsing, and secure handling
of sensitive data such as database passwords and bot tokens.
"""

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SqlEngineConfig(BaseModel):
    """Configuration for the SQLAlchemy asynchronous engine connection pool."""

    pool_pre_ping: bool = True
    pool_recycle: int = 3600
    pool_size: int = 5
    max_overflow: int = 10
    echo: bool = False
    statement_timeout_seconds: int = Field(default=30, ge=0)
    command_timeout_seconds: int = Field(default=35, ge=0)


class SqlSessionConfig(BaseModel):
    """Configuration for the SQLAlchemy asynchronous session factory."""

    expire_on_commit: bool = False


class DatabaseSettings(BaseModel):
    """PostgreSQL database connection and pooling settings."""

    postgres_user: str
    postgres_password: SecretStr
    postgres_host: str
    postgres_port: int = 5432
    postgres_db: str

    engine: SqlEngineConfig = Field(default_factory=SqlEngineConfig)
    session: SqlSessionConfig = Field(default_factory=SqlSessionConfig)

    @property
    def db_url(self) -> str:
        """
        Generate the asynchronous database connection URL.

        Returns:
            str: The formatted asyncpg connection string.
        """
        return (
            f"postgresql+asyncpg://{self.postgres_user}:"
            f"{self.postgres_password.get_secret_value()}@"
            f"{self.postgres_host}:{self.postgres_port}/"
            f"{self.postgres_db}"
        )


class BotSettings(BaseModel):
    """Telegram Bot configuration settings."""

    token: SecretStr = Field(min_length=1)
    proxy: str | None = None

    @field_validator("proxy", mode="before")
    @classmethod
    def _empty_proxy_to_none(cls, value: object) -> object:
        """
        Normalise an unset proxy to ``None``.

        Deployment configs (``.env.example``, Docker Compose) express "no proxy"
        as an empty ``BOT__PROXY=`` value. Treating it as ``None`` keeps callers
        such as ``main.py`` from handing a blank URL to the HTTP session.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value


class AISettings(BaseModel):
    enabled: bool = True
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str
    request_timeout_seconds: int = Field(default=60, gt=0)
    prompt_version: str = "tutor_v2"
    num_ctx: int = 4096


class Settings(BaseSettings):
    """
    Root application settings.

    Aggregates bot and database configurations and handles environment
    variable parsing with nested delimiter support.
    """

    bot: BotSettings
    db: DatabaseSettings
    ai: AISettings

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_nested_delimiter="__",
    )


_settings: Settings | None = None


def get_settings() -> Settings:
    """
    Lazily initialise and return the global ``Settings`` instance.

    On first call the settings are loaded from environment variables / ``.env``
    file.  Subsequent calls return the cached instance.

    Raises:
        pydantic.ValidationError: If required environment variables are missing
            or contain invalid values.
    """
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
