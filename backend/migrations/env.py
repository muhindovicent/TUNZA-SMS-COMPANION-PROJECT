"""Alembic uses factory configuration, never credentials from alembic.ini."""

from logging.config import fileConfig

from alembic import context
from flask import current_app
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from tunza.extensions import db

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = db.metadata
database_url = current_app.config["MIGRATION_DATABASE_URL"]
if database_url is None:
    raise RuntimeError("MIGRATION_DATABASE_URL is required for migrations.")
if database_url.username == current_app.config["SQLALCHEMY_DATABASE_URI"].username:
    raise RuntimeError("Migration and runtime database users must be distinct.")


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(
        database_url,
        poolclass=NullPool,
        hide_parameters=True,
        connect_args=current_app.config["SQLALCHEMY_ENGINE_OPTIONS"]["connect_args"],
    )
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection, target_metadata=target_metadata, compare_type=True
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
