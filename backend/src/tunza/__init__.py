"""TUNZA application factory with health, authentication and facility scope APIs."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from flask import Flask

from tunza.config import build_config
from tunza.csrf import register_csrf_protection
from tunza.database_security import register_runtime_role
from tunza.extensions import db, migrate
from tunza.http import register_http_handlers
from tunza.services.rate_limit import SlidingWindowRateLimiter


def create_app(config: Mapping[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(build_config(config))

    db.init_app(app)
    register_runtime_role(app)
    migrate.init_app(app, db, directory=str(Path(__file__).resolve().parents[2] / "migrations"))
    register_http_handlers(app)
    register_csrf_protection(app)
    app.extensions["tunza.login_rate_limiter"] = SlidingWindowRateLimiter(
        app.config["LOGIN_RATE_LIMIT_ATTEMPTS"],
        app.config["LOGIN_RATE_LIMIT_WINDOW_SECONDS"],
    )

    from tunza.appointments import blueprint as appointments_blueprint
    from tunza.appointments import routes as appointment_routes  # noqa: F401
    from tunza.content import blueprint as content_blueprint
    from tunza.content import routes as content_routes  # noqa: F401
    from tunza.health import blueprint as health_blueprint
    from tunza.identity import admin as identity_admin  # noqa: F401
    from tunza.identity import admin_blueprint, auth_blueprint, facilities_blueprint
    from tunza.identity import routes as identity_routes  # noqa: F401
    from tunza.patients import blueprint as patients_blueprint
    from tunza.patients import routes as patient_routes  # noqa: F401
    from tunza.pregnancy import blueprint as pregnancy_blueprint
    from tunza.pregnancy import completion as completion_routes  # noqa: F401
    from tunza.pregnancy import routes as pregnancy_routes  # noqa: F401
    from tunza.transfers import blueprint as transfers_blueprint
    from tunza.transfers import routes as transfer_routes  # noqa: F401

    app.register_blueprint(health_blueprint, url_prefix="/api/v1/health")
    app.register_blueprint(auth_blueprint)
    app.register_blueprint(facilities_blueprint)
    app.register_blueprint(admin_blueprint)
    app.register_blueprint(patients_blueprint)
    app.register_blueprint(pregnancy_blueprint)
    app.register_blueprint(appointments_blueprint)
    app.register_blueprint(transfers_blueprint)
    app.register_blueprint(content_blueprint)

    from tunza.commands import seed_synthetic_identity

    app.cli.add_command(seed_synthetic_identity)
    return app
