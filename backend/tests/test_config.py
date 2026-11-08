import pytest

from tunza import create_app
from tunza.config import ConfigurationError, build_config, postgres_url
from tunza.extensions import db


@pytest.mark.parametrize("value", ["", "short", " " * 40])
def test_missing_or_weak_secret_fails_closed(settings, value):
    settings["SECRET_KEY"] = value
    with pytest.raises(ConfigurationError, match="SECRET_KEY"):
        create_app(settings)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "sqlite:///synthetic.db",
        "mysql://synthetic@localhost/test",
        "postgresql://synthetic@localhost",
        "postgresql://synthetic@localhost:not-a-port/test",
    ],
)
def test_invalid_database_configuration_fails_without_exposing_values(settings, url):
    settings["DATABASE_URL"] = url
    with pytest.raises(ConfigurationError) as raised:
        create_app(settings)
    assert "DATABASE_URL" in str(raised.value)
    assert "synthetic" not in str(raised.value)


def test_postgres_url_normalization():
    url = postgres_url("postgres://synthetic@127.0.0.1/tunza_unit_test")
    assert url.drivername == "postgresql+psycopg"


@pytest.mark.parametrize("value", ["invalid", "0", "31"])
def test_invalid_connection_timeout_rejected(settings, value):
    settings["DATABASE_CONNECT_TIMEOUT"] = value
    with pytest.raises(ConfigurationError, match="DATABASE_CONNECT_TIMEOUT"):
        build_config(settings)


def test_unknown_environment_rejected(settings):
    settings["APP_ENV"] = "unknown"
    with pytest.raises(ConfigurationError, match="APP_ENV"):
        build_config(settings)


def test_separate_migration_url_validated(settings):
    settings["MIGRATION_DATABASE_URL"] = "sqlite:///not-postgres.db"
    with pytest.raises(ConfigurationError, match="MIGRATION_DATABASE_URL"):
        build_config(settings)


def test_factory_supports_isolated_instances_without_connecting(settings):
    first = create_app(settings)
    settings["SECRET_KEY"] = settings["SECRET_KEY"] + "second-instance"
    second = create_app(settings)
    assert first is not second
    assert first.config["SECRET_KEY"] != second.config["SECRET_KEY"]
    assert first.config["DEBUG"] is False
    assert first.config["SQLALCHEMY_ECHO"] is False
    with first.app_context():
        first_engine = db.engine
        assert "users" in db.metadata.tables
        assert "auth_sessions" in db.metadata.tables
    with second.app_context():
        assert db.engine is not first_engine
        db.engine.dispose()
    first_engine.dispose()
