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


class DownloadCookieSettings(BaseSettings):
    """Only optional cookie configuration; no production token/database fallback."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # External readonly Netscape files; only authenticated owner !video jobs.
    download_cookie_owner_id: int | None = Field(default=None, gt=0, alias="DOWNLOAD_COOKIE_OWNER_ID")
    youtube_cookie_file: Path | None = Field(default=None, alias="YOUTUBE_COOKIE_FILE", repr=False)
    instagram_cookie_file: Path | None = Field(default=None, alias="INSTAGRAM_COOKIE_FILE", repr=False)
    tiktok_cookie_file: Path | None = Field(default=None, alias="TIKTOK_COOKIE_FILE", repr=False)

    @field_validator("download_cookie_owner_id", "youtube_cookie_file", "instagram_cookie_file",
                     "tiktok_cookie_file", mode="before")
    @classmethod
    def empty_cookie_setting_disables(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value


class Settings(DownloadCookieSettings):
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

    database_pool_size: int = Field(default=5, ge=1, le=15, alias="DATABASE_POOL_SIZE")
    database_max_overflow: int = Field(default=5, ge=0, le=10, alias="DATABASE_MAX_OVERFLOW")

    database_auto_init: bool = Field(default=True, alias="DATABASE_AUTO_INIT")

    # --- Dashboard / auth -------------------------------------------------
    dashboard_admin_username: str = Field("admin", alias="DASHBOARD_ADMIN_USERNAME")
    dashboard_admin_password: str = Field("changeme", alias="DASHBOARD_ADMIN_PASSWORD")
    session_secret: str = Field(..., alias="SESSION_SECRET")
    session_max_age_seconds: int = Field(
        default=60 * 60 * 12, alias="SESSION_MAX_AGE_SECONDS"
    )

    # Stable Telegram identity; unset disables all administrator privileges.
    miniapp_admin_id: int | None = Field(default=None, gt=0, alias="MINIAPP_ADMIN_ID")

    @field_validator("miniapp_admin_id", mode="before")
    @classmethod
    def empty_admin_id_disables_admin(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value

    def validate_webhook_security(self) -> None:
        """Fail before DB/network activity when production webhook is unprotected."""
        import re

        secret = self.telegram_webhook_secret
        if self.environment.lower() in {"production", "prod"} and not secret:
            raise RuntimeError("Production requires TELEGRAM_WEBHOOK_SECRET")
        if secret and not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", secret):
            raise RuntimeError("Invalid TELEGRAM_WEBHOOK_SECRET format")

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
