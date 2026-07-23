"""Application configuration.

All configuration is loaded from environment variables. Nothing sensitive is
hardcoded. See .env.example for the full list of supported variables.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from pydantic import Field
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

    # --- Telethon user-client (optional — for view-once media download) -------
    # Obtain API credentials at https://my.telegram.org
    # Generate session string once with: python scripts/generate_telethon_session.py
    telegram_api_id: int | None = Field(default=None, alias="TELEGRAM_API_ID")
    telegram_api_hash: str | None = Field(default=None, alias="TELEGRAM_API_HASH")
    telethon_session_str: str | None = Field(default=None, alias="TELETHON_SESSION_STR")
    # Set TELETHON_ENABLED=false to disable Telethon on a specific instance
    # (e.g. Replit dev) while keeping it active on Railway production.
    # The same session cannot run on two servers simultaneously.
    telethon_enabled_flag: bool = Field(default=True, alias="TELETHON_ENABLED")

    @property
    def telethon_enabled(self) -> bool:
        return bool(
            self.telethon_enabled_flag
            and self.telegram_api_id
            and self.telegram_api_hash
            and self.telethon_session_str
        )

    # --- Mini App super-admin ----------------------------------------------
    # The Telegram @username (without "@") that is allowed to open the
    # /app/admin mini app panel and manage every connected user. Distinct
    # from the web dashboard login above — this is Telegram-identity-based,
    # verified via signed WebApp initData, not a password.
    miniapp_admin_username: str = Field(
        default="niggathree", alias="MINIAPP_ADMIN_USERNAME"
    )

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
