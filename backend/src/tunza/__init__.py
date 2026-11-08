"""TUNZA application factory with health, authentication and facility scope APIs."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from flask import Flask

from tunza.config import build_config
from tunza.csrf import register_csrf_protection
from tunza.extensions import db, migrate
from tunza.http import register_http_handlers
from tunza.services.rate_limit import SlidingWindowRateLimiter


def create_app(config: Mapping[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(build_config(config))

    db.init_app(app)
    migrate.init_app(app, db, directory=str(Path(__file__).resolve().parents[2] / "migrations"))
    register_http_handlers(app)
    register_csrf_protection(app)
    app.extensions["tunza.login_rate_limiter"] = SlidingWindowRateLimiter(
        app.config["LOGIN_RATE_LIMIT_ATTEMPTS"],
        app.config["LOGIN_RATE_LIMIT_WINDOW_SECONDS"],
    )

    from tunza.health import blueprint as health_blueprint
    from tunza.identity import auth_blueprint, facilities_blueprint

    app.register_blueprint(health_blueprint, url_prefix="/api/v1/health")
    app.register_blueprint(auth_blueprint)
    app.register_blueprint(facilities_blueprint)

    from tunza.commands import seed_synthetic_identity

    app.cli.add_command(seed_synthetic_identity)
    return app
