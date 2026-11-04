"""Application configuration.

All configuration is loaded from environment variables. Nothing sensitive is
hardcoded. See .env.example for the full list of supported variables.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Central application settings, populated from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Telegram -----------------------------------------------------
    telegram_bot_token: str = Field(..., alias="TELEGRAM_BOT_TOKEN")
    telegram_webhook_secret: str = Field(
        default="", alias="TELEGRAM_WEBHOOK_SECRET"
    )
    webhook_path: str = Field(default="/webhook", alias="WEBHOOK_PATH")
    webhook_base_url: str = Field(default="", alias="WEBHOOK_BASE_URL")

    # --- Server ---------------------------------------------------------
    port: int = Field(default=8000, alias="PORT")
    host: str = Field(default="0.0.0.0", alias="HOST")
    environment: str = Field(default="production", alias="ENVIRONMENT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # --- Database ---------------------------------------------------------
    database_url: str = Field(
        default=f"sqlite+aiosqlite:///{BASE_DIR}/data/bot.db",
        alias="DATABASE_URL",
    )

    # --- Dashboard / auth -------------------------------------------------
    dashboard_admin_username: str = Field("admin", alias="DASHBOARD_ADMIN_USERNAME")
    dashboard_admin_password: str = Field("changeme", alias="DASHBOARD_ADMIN_PASSWORD")
    session_secret: str = Field(..., alias="SESSION_SECRET")
    session_max_age_seconds: int = Field(
        default=60 * 60 * 12, alias="SESSION_MAX_AGE_SECONDS"
    )

    # --- Mini App super-admin ----------------------------------------------
    # The Telegram user ID allowed to manage the bot. Unset disables access.
    miniapp_admin_telegram_id: int | None = Field(
        default=None, alias="MINIAPP_ADMIN_TELEGRAM_ID"
    )

    @field_validator("miniapp_admin_telegram_id", mode="before")
    @classmethod
    def _parse_miniapp_admin_telegram_id(cls, value: object) -> int | None:
        if isinstance(value, str):
            value = value.strip()
            if not (value.isascii() and value.isdecimal() and len(value) <= 16):
                return None
            value = int(value)
        # Telegram user IDs are positive integers with at most 52 significant bits.
        if type(value) is int and 0 < value < 2**52:
            return value
        return None

    def is_miniapp_admin(self, user_id: object) -> bool:
        return type(user_id) is int and user_id == self.miniapp_admin_telegram_id

    @property
    def normalized_database_url(self) -> str:
        """Return a database URL with an async-capable driver.

        Accepts plain `postgres://` / `postgresql://` URLs (as commonly
        provided by hosting platforms) and rewrites them to use the asyncpg
        driver. SQLite URLs are passed through untouched if already async,
        or rewritten to use aiosqlite.
        """

        url = self.database_url
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://") and "+asyncpg" not in url:
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif url.startswith("sqlite://") and "+aiosqlite" not in url:
            url = url.replace("sqlite://", "sqlite+aiosqlite://", 1)

        if "+asyncpg" in url:
            # asyncpg does not accept libpq-style query params such as
            # `sslmode`/`channel_binding` (commonly injected by hosting
            # platforms like Replit, Heroku, Supabase). Strip them here;
            # SSL is negotiated automatically by asyncpg when supported by
            # the server, and `ssl_context` can be added via connect_args
            # if a deployment target requires it explicitly.
            split = urlsplit(url)
            query = parse_qs(split.query)
            for unsupported in ("sslmode", "channel_binding"):
                query.pop(unsupported, None)
            url = urlunsplit(
                (
                    split.scheme,
                    split.netloc,
                    split.path,
                    urlencode(query, doseq=True),
                    split.fragment,
                )
            )
        return url

    @property
    def is_sqlite(self) -> bool:
        return "sqlite" in self.normalized_database_url

    @property
    def is_postgres(self) -> bool:
        return "postgresql" in self.normalized_database_url

    @property
    def requires_ssl(self) -> bool:
        """Whether the original DATABASE_URL requested SSL (e.g. sslmode=require)."""

        return "sslmode=require" in self.database_url or "sslmode=verify" in self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
