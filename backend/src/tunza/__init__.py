"""TUNZA application factory. Phase 1A exposes infrastructure checks only."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from flask import Flask

from tunza.config import build_config
from tunza.extensions import db, migrate
from tunza.http import register_http_handlers


def create_app(config: Mapping[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(build_config(config))

    db.init_app(app)
    migrate.init_app(app, db, directory=str(Path(__file__).resolve().parents[2] / "migrations"))
    register_http_handlers(app)

    from tunza.health import blueprint as health_blueprint

    app.register_blueprint(health_blueprint, url_prefix="/api/v1/health")
    return app
