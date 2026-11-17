"""Alembic environment configuration for async SQLAlchemy migrations."""

from __future__ import annotations

import asyncio

# Import every model, including optional feature tables, for complete drift checks.
import importlib
import os
import pkgutil
from logging.config import fileConfig

from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

import app.models
from alembic import context
from app.config import get_settings
from app.database.base import Base

for module in pkgutil.iter_modules(app.models.__path__):
    importlib.import_module(f"app.models.{module.name}")


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

settings = get_settings()
if not settings.normalized_database_url.startswith("postgresql+asyncpg://"):
    raise RuntimeError("Clean migrations require an explicit PostgreSQL database URL")
migration_role = os.environ.get("SAVELOG_MIGRATION_ROLE", "")
if migration_role not in {"", "savelog_owner"}:
    raise RuntimeError("Unsupported migration role")
if settings.environment.lower() in {"production", "prod"} and migration_role != "savelog_owner":
    raise RuntimeError("Production migrations require SAVELOG_MIGRATION_ROLE=savelog_owner")
config.set_main_option("sqlalchemy.url", settings.normalized_database_url.replace("%", "%%"))


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        if migration_role:
            context.execute("SET ROLE savelog_owner")
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        if migration_role:
            # Explicit fixed role owns objects; never interpolate an env role.
            await connection.execute(text("SET ROLE savelog_owner"))
            await connection.commit()
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
