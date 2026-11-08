from unittest.mock import MagicMock
from uuid import UUID

from sqlalchemy.exc import OperationalError

from tunza.health import routes
from tunza.services.database import database_is_available


def test_liveness_does_not_require_database(client, monkeypatch):
    def unexpected_database_call(_engine):
        raise AssertionError("Liveness must not touch the database")

    monkeypatch.setattr(routes, "database_is_available", unexpected_database_call)
    response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json == {"data": {"service": "tunza-api", "status": "ok"}}
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert UUID(response.headers["X-Request-ID"])


def test_readiness_reports_database_connectivity(client, monkeypatch):
    monkeypatch.setattr(routes, "database_is_available", lambda _engine: True)
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    assert response.json["data"] == {
        "service": "tunza-api",
        "status": "ready",
        "checks": {"database": "connected"},
    }


def test_database_outage_returns_503_but_liveness_stays_up(client, monkeypatch):
    monkeypatch.setattr(routes, "database_is_available", lambda _engine: False)
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json["data"]["checks"] == {"database": "unavailable"}
    assert response.json["data"]["status"] == "not_ready"
    assert client.get("/api/v1/health/live").status_code == 200


def test_connectivity_check_is_read_only_and_releases_connection():
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.scalar_one.return_value = 1
    assert database_is_available(engine) is True
    assert str(connection.execute.call_args.args[0]) == "SELECT 1"
    engine.connect.return_value.__exit__.assert_called_once()
    connection.commit.assert_not_called()


def test_database_failure_does_not_leak_driver_details(caplog):
    engine = MagicMock()
    engine.connect.side_effect = OperationalError(
        "SELECT 1", {}, Exception("synthetic-sensitive-connection-detail")
    )
    assert database_is_available(engine) is False
    assert "synthetic-sensitive-connection-detail" not in caplog.text


def test_missing_route_is_json_and_uses_server_request_id(client):
    response = client.get("/api/v1/not-present", headers={"X-Request-ID": "client-controlled"})
    assert response.status_code == 404
    assert response.json["error"]["code"] == "not_found"
    assert response.json["error"]["request_id"] == response.headers["X-Request-ID"]
    assert response.headers["X-Request-ID"] != "client-controlled"


def test_health_rejects_writes_and_preserves_allow_header(client):
    response = client.post("/api/v1/health/live")
    assert response.status_code == 405
    assert "GET" in response.headers["Allow"]
    assert response.json["error"]["code"] == "method_not_allowed"


def test_unexpected_errors_are_sanitized(app, caplog):
    def broken_route():
        raise RuntimeError("synthetic-sensitive-failure")

    app.add_url_rule("/test-only-broken-route", view_func=broken_route)
    response = app.test_client().get("/test-only-broken-route")
    assert response.status_code == 500
    assert response.json["error"]["code"] == "internal_error"
    assert "synthetic-sensitive-failure" not in response.text
    assert "synthetic-sensitive-failure" not in caplog.text
