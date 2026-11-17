"""Read-only startup/readiness checks. Never prints credentials or SQL errors."""

from __future__ import annotations

import asyncio
import importlib
import os
import pkgutil
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def validate_environment() -> None:
    for name in (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_WEBHOOK_SECRET",
        "SESSION_SECRET",
        "SAVELOG_SCHEMA_REVISION",
    ):
        value = os.environ.get(name, "")
        if not value or value.lower().startswith(("replace", "changeme", "pending")):
            raise ValueError("Required deployment setting is missing")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", os.environ["TELEGRAM_WEBHOOK_SECRET"]):
        raise ValueError("Invalid webhook secret")
    if len(os.environ["SESSION_SECRET"]) < 32:
        raise ValueError("Session secret is too short")
    if os.environ.get("DATABASE_AUTO_INIT", "").lower() != "false":
        raise ValueError("Automatic DDL must be disabled")
    if not os.environ.get("DATABASE_URL", "").startswith("postgresql+asyncpg://"):
        raise ValueError("PostgreSQL is required")
    if not os.environ.get("WEBHOOK_BASE_URL", "").startswith("https://"):
        raise ValueError("HTTPS is required")
    if os.environ.get("WEBHOOK_PATH") != "/webhook":
        raise ValueError("Unsupported webhook path")


async def check_database(check_schema: bool) -> None:
    from sqlalchemy import inspect, text

    from app.database.session import dispose_engine, get_engine

    try:
        async with get_engine().connect() as connection:
            await connection.execute(text("SELECT 1"))
            if not check_schema:
                return
            revisions = (
                (await connection.execute(text("SELECT version_num FROM alembic_version")))
                .scalars()
                .all()
            )
            if revisions != [os.environ["SAVELOG_SCHEMA_REVISION"]]:
                raise ValueError("Unapproved database revision")
            import app.models
            from app.database.base import Base

            for module in pkgutil.iter_modules(app.models.__path__):
                importlib.import_module(f"app.models.{module.name}")

            def verify(sync_connection):
                inspector = inspect(sync_connection)
                for table in Base.metadata.sorted_tables:
                    columns = {column["name"] for column in inspector.get_columns(table.name)}
                    if not set(table.columns.keys()).issubset(columns):
                        raise ValueError("Database schema is incomplete")

            await connection.run_sync(verify)
    finally:
        await dispose_engine()


def main() -> int:
    try:
        validate_environment()
        health = "--health" in sys.argv
        if health:
            with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3) as response:
                if response.status != 200:
                    raise ValueError("HTTP health failed")
        asyncio.run(check_database(check_schema=not health))
    except Exception:
        print(
            "Save Log readiness failed; check configuration and approved database schema privately",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
