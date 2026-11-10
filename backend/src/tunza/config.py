"""Explicit environment-based configuration, with no credential fallbacks."""

import base64
import hashlib
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

ENV_FILE = Path(__file__).resolve().parents[3] / ".env"
_ROLE_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_KEY_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class ConfigurationError(ValueError):
    """A safe-to-display configuration failure that never includes secret values."""


def postgres_url(value: str | URL | None, name: str = "DATABASE_URL") -> URL:
    """Validate and normalize PostgreSQL URLs without echoing invalid input."""
    try:
        url = make_url(value or "")
    except (ArgumentError, TypeError, ValueError):
        raise ConfigurationError(f"{name} must be a valid PostgreSQL connection URL.") from None

    if url.drivername not in {"postgres", "postgresql", "postgresql+psycopg"}:
        raise ConfigurationError(f"{name} must use PostgreSQL with the psycopg driver.")
    if not url.host or not url.username or not url.database:
        raise ConfigurationError(f"{name} requires a host, username, and database name.")
    try:
        if url.port is not None and not 1 <= url.port <= 65535:
            raise ValueError
    except ValueError:
        raise ConfigurationError(f"{name} contains an invalid port.") from None
    return url.set(drivername="postgresql+psycopg")


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ConfigurationError(f"{name} must be an integer.") from None
    if not minimum <= number <= maximum:
        raise ConfigurationError(f"{name} is outside its supported range.")
    return number


def _boolean(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}:
        return True
    if isinstance(value, str) and value.strip().lower() in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true or false.")


def _patient_encryption_key(values: Mapping[str, Any], environment: str, secret: str) -> bytes:
    encoded = values.get("PATIENT_ENCRYPTION_KEY", "")
    if encoded:
        if not isinstance(encoded, str):
            raise ConfigurationError("PATIENT_ENCRYPTION_KEY must be base64url text.")
        try:
            key = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        except (ValueError, TypeError):
            raise ConfigurationError(
                "PATIENT_ENCRYPTION_KEY must be valid base64url text."
            ) from None
        if len(key) != 32:
            raise ConfigurationError("PATIENT_ENCRYPTION_KEY must decode to exactly 32 bytes.")
        return key
    if environment == "production":
        raise ConfigurationError("PATIENT_ENCRYPTION_KEY is required in production.")
    return hashlib.sha256(f"tunza-patient-development-key:{secret}".encode()).digest()


def build_config(overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    # Use the explicit repository file, never a parent directory's unrelated .env.
    # Process environment values always win. Tests can supply all required values.
    load_dotenv(ENV_FILE, override=False)
    values = dict(os.environ)
    values.update(overrides or {})

    environment = values.get("APP_ENV", "development")
    if environment not in {"development", "testing", "production"}:
        raise ConfigurationError("APP_ENV must be development, testing, or production.")

    secret = values.get("SECRET_KEY", "")
    if not isinstance(secret, str) or len(secret.strip()) < 32:
        raise ConfigurationError(
            "SECRET_KEY must contain at least 32 characters generated locally."
        )

    runtime_role = values.get("DATABASE_RUNTIME_ROLE", "tunza_runtime")
    if not isinstance(runtime_role, str) or not _ROLE_PATTERN.fullmatch(runtime_role):
        raise ConfigurationError("DATABASE_RUNTIME_ROLE must be a safe PostgreSQL role name.")
    patient_key_version = values.get("PATIENT_KEY_VERSION", "synthetic-v1")
    if not isinstance(patient_key_version, str) or not _KEY_VERSION_PATTERN.fullmatch(
        patient_key_version
    ):
        raise ConfigurationError("PATIENT_KEY_VERSION must be a short safe identifier.")
    if environment == "production" and "PATIENT_KEY_VERSION" not in values:
        raise ConfigurationError("PATIENT_KEY_VERSION is required in production.")
    patient_encryption_key = _patient_encryption_key(values, environment, secret)

    database_url = postgres_url(values.get("DATABASE_URL"))
    migration_url = values.get("MIGRATION_DATABASE_URL")
    connect_timeout = _integer(
        values.get("DATABASE_CONNECT_TIMEOUT", 3), "DATABASE_CONNECT_TIMEOUT", 1, 30
    )
    pool_timeout = _integer(values.get("DATABASE_POOL_TIMEOUT", 3), "DATABASE_POOL_TIMEOUT", 1, 30)
    statement_timeout = _integer(
        values.get("DATABASE_STATEMENT_TIMEOUT_MS", 3000),
        "DATABASE_STATEMENT_TIMEOUT_MS",
        100,
        30000,
    )

    session_idle_timeout = _integer(
        values.get("SESSION_IDLE_TIMEOUT_SECONDS", 1800),
        "SESSION_IDLE_TIMEOUT_SECONDS",
        60,
        86400,
    )
    session_absolute_timeout = _integer(
        values.get("SESSION_ABSOLUTE_TIMEOUT_SECONDS", 43200),
        "SESSION_ABSOLUTE_TIMEOUT_SECONDS",
        300,
        604800,
    )
    csrf_token_max_age = _integer(
        values.get("CSRF_TOKEN_MAX_AGE_SECONDS", 900),
        "CSRF_TOKEN_MAX_AGE_SECONDS",
        60,
        3600,
    )
    login_rate_attempts = _integer(
        values.get("LOGIN_RATE_LIMIT_ATTEMPTS", 5),
        "LOGIN_RATE_LIMIT_ATTEMPTS",
        1,
        100,
    )
    login_rate_window = _integer(
        values.get("LOGIN_RATE_LIMIT_WINDOW_SECONDS", 300),
        "LOGIN_RATE_LIMIT_WINDOW_SECONDS",
        10,
        3600,
    )

    return {
        "APP_ENV": environment,
        "TESTING": environment == "testing",
        "DEBUG": False,
        "SECRET_KEY": secret,
        "SQLALCHEMY_DATABASE_URI": database_url,
        "MIGRATION_DATABASE_URL": (
            postgres_url(migration_url, "MIGRATION_DATABASE_URL") if migration_url else None
        ),
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "SQLALCHEMY_ECHO": False,
        "DATABASE_RUNTIME_ROLE": runtime_role,
        "PATIENT_ENCRYPTION_KEY": patient_encryption_key,
        "PATIENT_KEY_VERSION": patient_key_version,
        "SQLALCHEMY_ENGINE_OPTIONS": {
            "pool_pre_ping": True,
            "pool_size": 5,
            "max_overflow": 0,
            "pool_timeout": pool_timeout,
            "pool_recycle": 1800,
            "hide_parameters": True,
            "connect_args": {
                "connect_timeout": connect_timeout,
                "options": f"-c statement_timeout={statement_timeout}",
            },
        },
        "MAX_CONTENT_LENGTH": 64 * 1024,
        "SESSION_COOKIE_NAME": values.get("SESSION_COOKIE_NAME", "tunza_session"),
        "CSRF_COOKIE_NAME": values.get("CSRF_COOKIE_NAME", "tunza_csrf"),
        "SESSION_COOKIE_SECURE": _boolean(
            values.get("SESSION_COOKIE_SECURE", True), "SESSION_COOKIE_SECURE"
        ),
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_IDLE_TIMEOUT_SECONDS": session_idle_timeout,
        "SESSION_ABSOLUTE_TIMEOUT_SECONDS": session_absolute_timeout,
        "CSRF_TOKEN_MAX_AGE_SECONDS": csrf_token_max_age,
        "LOGIN_RATE_LIMIT_ATTEMPTS": login_rate_attempts,
        "LOGIN_RATE_LIMIT_WINDOW_SECONDS": login_rate_window,
    }
