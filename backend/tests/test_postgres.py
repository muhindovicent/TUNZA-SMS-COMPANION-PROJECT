"""Opt-in migration/connectivity test against an empty disposable database only."""

import os
import secrets

import pytest
from sqlalchemy import inspect, text

from tunza import create_app
from tunza.config import postgres_url
from tunza.extensions import db


@pytest.mark.integration
def test_postgres_migration_roundtrip_and_readiness():
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated empty local tunza_*_test database.")
    url = postgres_url(value, "TEST_DATABASE_URL")
    if (
        url.host not in {"127.0.0.1", "localhost", "::1"}
        or not url.database.startswith("tunza_")
        or not url.database.endswith("_test")
    ):
        pytest.fail("Integration tests require a dedicated local tunza_*_test database.")

    migration_value = os.environ.get("TEST_MIGRATION_DATABASE_URL", value)
    migration_url = postgres_url(migration_value, "TEST_MIGRATION_DATABASE_URL")
    if (migration_url.host, migration_url.port, migration_url.database) != (
        url.host,
        url.port,
        url.database,
    ):
        pytest.fail("The migration test connection must target the same disposable local database.")

    application = create_app(
        {
            "APP_ENV": "testing",
            "SECRET_KEY": secrets.token_urlsafe(48),
            "DATABASE_URL": value,
            "MIGRATION_DATABASE_URL": migration_value,
        }
    )
    with application.app_context():
        try:
            tables = inspect(db.engine).get_table_names()
            assert set(tables) <= {"alembic_version"}, "Use an empty disposable test database."
            runner = application.test_cli_runner()
            for _ in range(2):
                result = runner.invoke(args=["db", "upgrade"])
                assert result.exit_code == 0, result.output
            assert inspect(db.engine).get_table_names() == ["alembic_version"]
            with db.engine.connect() as connection:
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                assert revision == "0001_foundation"
            response = application.test_client().get("/api/v1/health/ready")
            assert response.status_code == 200
            assert response.json["data"]["checks"]["database"] == "connected"
            result = runner.invoke(args=["db", "check"])
            assert result.exit_code == 0, result.output
            result = runner.invoke(args=["db", "downgrade", "base"])
            assert result.exit_code == 0, result.output
            with db.engine.connect() as connection:
                revision_count = connection.execute(
                    text("SELECT count(*) FROM alembic_version")
                ).scalar_one()
                assert revision_count == 0
            result = runner.invoke(args=["db", "upgrade"])
            assert result.exit_code == 0, result.output
        finally:
            db.session.remove()
            db.engine.dispose()
