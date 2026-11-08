"""Liveness is independent from database readiness."""

from flask import current_app, g, jsonify

from tunza.extensions import db
from tunza.health import blueprint
from tunza.services.database import database_is_available


@blueprint.get("/live")
def live():
    return jsonify(data={"service": "tunza-api", "status": "ok"})


@blueprint.get("/ready")
def ready():
    connected = database_is_available(db.engine)
    if not connected:
        current_app.logger.warning("database_health_unavailable request_id=%s", g.request_id)
    return jsonify(
        data={
            "service": "tunza-api",
            "status": "ready" if connected else "not_ready",
            "checks": {"database": "connected" if connected else "unavailable"},
        }
    ), (200 if connected else 503)
