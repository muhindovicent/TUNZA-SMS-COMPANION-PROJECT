def test_protected_routes_require_a_session_without_touching_the_database(client):
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/facilities").status_code == 401


def test_login_rejects_malformed_bodies_before_authentication(client):
    csrf = client.get("/api/v1/auth/csrf").json["data"]["csrf_token"]
    response = client.post(
        "/api/v1/auth/login",
        data="{not-json",
        content_type="application/json",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422
    assert response.json["error"]["code"] == "validation_failed"

    response = client.post(
        "/api/v1/auth/login",
        json={"login_identifier": 42, "password": ["not", "a", "string"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422
    assert response.json["error"]["code"] == "validation_failed"


def test_login_enforces_rate_limit_without_credentials_or_database(client, app):
    limiter = app.extensions["tunza.login_rate_limiter"]
    for _ in range(app.config["LOGIN_RATE_LIMIT_ATTEMPTS"]):
        limiter.record_failure("login:ip:127.0.0.1")
        limiter.record_failure("login:id:synthetic")

    csrf = client.get("/api/v1/auth/csrf").json["data"]["csrf_token"]
    response = client.post(
        "/api/v1/auth/login",
        json={"login_identifier": "synthetic", "password": "synthetic-password"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 429
    assert response.json["error"]["code"] == "rate_limited"
    assert (
        1 <= int(response.headers["Retry-After"]) <= app.config["LOGIN_RATE_LIMIT_WINDOW_SECONDS"]
    )


def test_public_registration_routes_do_not_exist(client):
    csrf = client.get("/api/v1/auth/csrf").json["data"]["csrf_token"]
    headers = {"X-CSRF-Token": csrf}
    assert client.post("/api/v1/auth/register", json={}, headers=headers).status_code == 404
    assert client.post("/api/v1/auth/signup", json={}, headers=headers).status_code == 404
    # Staff provisioning exists but requires an authenticated administrator.
    assert client.post("/api/v1/users", json={}, headers=headers).status_code == 401
