"""Unit fixtures never connect to a real or pre-existing database."""

import secrets

import pytest

from tunza import create_app
from tunza.extensions import db


@pytest.fixture
def settings():
    return {
        "APP_ENV": "testing",
        "SECRET_KEY": secrets.token_urlsafe(48),
        "DATABASE_URL": "postgresql+psycopg://synthetic_test@127.0.0.1:1/tunza_unit_test",
        "MIGRATION_DATABASE_URL": "",
    }


@pytest.fixture
def app(settings):
    application = create_app(settings)
    yield application
    with application.app_context():
        db.session.remove()
        db.engine.dispose()


@pytest.fixture
def client(app):
    return app.test_client()
