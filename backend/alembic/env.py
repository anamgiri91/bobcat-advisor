import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, make_url, pool

from alembic import context
from app import (
    models,  # noqa: F401 — ensures models are registered on Base.metadata
    secrets,
)
from app.config import settings
from app.database import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL.replace("%", "%%"))


def describe_target() -> str:
    """Which database this run points at and where the setting came from, so a
    stale value (e.g. an old Secret File overriding the env var) is visible."""
    url = make_url(settings.DATABASE_URL)
    where = f"{url.host}:{url.port or 5432}/{url.database}" if url.host else url.database
    return f"database {where} (DATABASE_URL from {secrets.source_of('DATABASE_URL') or 'default'})"


if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    print(f"alembic: migrating {describe_target()}", file=sys.stderr, flush=True)
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
