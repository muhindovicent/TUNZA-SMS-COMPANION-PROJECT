"""Opt-in migration/connectivity test against an empty disposable database only."""

import os
import secrets

import pytest
from sqlalchemy import create_engine, inspect, text

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
    if migration_url.username == url.username:
        pytest.fail("Integration tests require separate runtime and migration database users.")

    application = create_app(
        {
            "APP_ENV": "testing",
            "SECRET_KEY": secrets.token_urlsafe(48),
            "DATABASE_URL": value,
            "MIGRATION_DATABASE_URL": migration_value,
        }
    )
    migration_engine = create_engine(migration_url, hide_parameters=True)
    with application.app_context():
        try:
            tables = inspect(db.engine).get_table_names()
            assert set(tables) <= {"alembic_version"}, "Use an empty disposable test database."
            runner = application.test_cli_runner()
            for _ in range(2):
                result = runner.invoke(args=["db", "upgrade"])
                assert result.exit_code == 0, result.output
            with migration_engine.connect() as connection:
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                assert revision == "0006_patient_eligibility_consent"
            expected_tables = {
                "alembic_version",
                "audit_events",
                "auth_sessions",
                "communication_preferences",
                "consent_events",
                "consent_notice_versions",
                "adult_eligibility_checks",
                "episode_facility_assignments",
                "facilities",
                "facility_memberships",
                "facility_patient_profiles",
                "participant_assignments",
                "patient_registration_idempotency",
                "patients",
                "permissions",
                "pregnancy_episodes",
                "protected_evidence",
                "role_bindings",
                "role_permissions",
                "roles",
                "users",
            }
            assert set(inspect(db.engine).get_table_names()) == expected_tables
            privileged_app = create_app(
                {
                    "APP_ENV": "testing",
                    "SECRET_KEY": secrets.token_urlsafe(48),
                    "DATABASE_URL": migration_value,
                    "MIGRATION_DATABASE_URL": migration_value,
                }
            )
            with privileged_app.app_context():
                with pytest.raises(RuntimeError, match="Runtime database login"):
                    db.engine.connect()
                db.engine.dispose()
            for grant, revoke in (
                ("ALTER ROLE tunza_runtime BYPASSRLS", "ALTER ROLE tunza_runtime NOBYPASSRLS"),
                (
                    "GRANT pg_read_all_data TO tunza_runtime",
                    "REVOKE pg_read_all_data FROM tunza_runtime",
                ),
            ):
                try:
                    with migration_engine.begin() as connection:
                        connection.execute(text(grant))
                    probe = create_app(
                        {
                            "APP_ENV": "testing",
                            "SECRET_KEY": secrets.token_urlsafe(48),
                            "DATABASE_URL": value,
                            "MIGRATION_DATABASE_URL": migration_value,
                        }
                    )
                    with probe.app_context():
                        with pytest.raises(RuntimeError, match="Runtime database login"):
                            db.engine.connect()
                        db.engine.dispose()
                finally:
                    with migration_engine.begin() as connection:
                        connection.execute(text(revoke))
            with db.engine.connect() as connection:
                session_user, current_user = connection.execute(
                    text("SELECT session_user, current_user")
                ).one()
                assert session_user == current_user == "tunza_runtime"
                assert connection.execute(
                    text(
                        "SELECT NOT rolsuper AND NOT rolbypassrls AND NOT rolcreatedb "
                        "AND NOT rolcreaterole AND NOT rolreplication "
                        "FROM pg_roles WHERE rolname = session_user"
                    )
                ).scalar_one()
                assert not connection.execute(
                    text("SELECT has_schema_privilege(current_user, 'public', 'CREATE')")
                ).scalar_one()
                assert not connection.execute(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = 'public' AND c.relowner = "
                        "(SELECT oid FROM pg_roles WHERE rolname = current_user))"
                    )
                ).scalar_one()
                connection.execute(
                    text("SELECT set_config('tunza.facility_id', :facility, true)"),
                    {"facility": "00000000-0000-4000-8000-000000000001"},
                )
            with db.engine.connect() as connection:
                assert (
                    connection.execute(text("SELECT current_user")).scalar_one() == "tunza_runtime"
                )
                assert not connection.execute(
                    text("SELECT current_setting('tunza.facility_id', true)")
                ).scalar_one()
                assert connection.execute(text("SELECT count(*) FROM patients")).scalar_one() == 0
            response = application.test_client().get("/api/v1/health/ready")
            assert response.status_code == 200
            assert response.json["data"]["checks"]["database"] == "connected"
            result = runner.invoke(args=["db", "check"])
            assert result.exit_code == 0, result.output
            result = runner.invoke(args=["db", "downgrade", "base"])
            assert result.exit_code == 0, result.output
            with migration_engine.connect() as connection:
                revision_count = connection.execute(
                    text("SELECT count(*) FROM alembic_version")
                ).scalar_one()
                assert revision_count == 0
            result = runner.invoke(args=["db", "upgrade"])
            assert result.exit_code == 0, result.output
        finally:
            db.session.remove()
            db.engine.dispose()
            migration_engine.dispose()
