from werkzeug.test import EnvironBuilder

from tunza.csrf import (
    CSRF_HEADER,
    csrf_token_is_valid,
    issue_csrf_token,
    issue_pre_auth_csrf,
)


def _context(app, *, path, method="POST", cookies=None, headers=None):
    builder = EnvironBuilder(path=path, method=method, headers=headers or {})
    if cookies:
        cookie_header = "; ".join(f"{name}={value}" for name, value in cookies.items())
        existing = builder.headers.get("Cookie")
        builder.headers["Cookie"] = f"{existing}; {cookie_header}" if existing else cookie_header
    return app.request_context(builder.get_environ())


def test_csrf_endpoint_issues_pre_auth_token_and_readable_nonce_cookie(client):
    response = client.get("/api/v1/auth/csrf")
    assert response.status_code == 200
    assert response.json["data"]["csrf_token"]
    assert response.headers["Cache-Control"] == "no-store"
    set_cookie = response.headers["Set-Cookie"]
    assert "tunza_csrf=" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=Lax" in set_cookie
    assert "HttpOnly" not in set_cookie


def test_missing_or_malformed_token_is_rejected_before_any_view(client):
    assert client.post("/api/v1/auth/login", json={}).status_code == 403
    assert (
        client.post(
            "/api/v1/auth/login",
            json={},
            headers={"X-CSRF-Token": "not-a-signed-token"},
        ).status_code
        == 403
    )
    assert client.get("/api/v1/auth/csrf").status_code == 200


def test_pre_auth_token_roundtrip_requires_matching_nonce_cookie(app):
    with app.app_context():
        token, nonce = issue_pre_auth_csrf()
    with _context(
        app,
        path="/api/v1/auth/login",
        cookies={"tunza_csrf": nonce},
        headers={CSRF_HEADER: token},
    ):
        assert csrf_token_is_valid(token) is True
    with _context(app, path="/api/v1/auth/login", headers={CSRF_HEADER: token}):
        assert csrf_token_is_valid(token) is False
    with _context(
        app,
        path="/api/v1/auth/login",
        cookies={"tunza_csrf": "different-nonce"},
        headers={CSRF_HEADER: token},
    ):
        assert csrf_token_is_valid(token) is False


def test_session_bound_token_validates_against_session_cookie_only(app):
    with app.app_context():
        token = issue_csrf_token("synthetic-session-token")
    with _context(
        app,
        path="/api/v1/facilities",
        cookies={"tunza_session": "synthetic-session-token"},
        headers={CSRF_HEADER: token},
    ):
        assert csrf_token_is_valid(token) is True
    with _context(
        app,
        path="/api/v1/facilities",
        cookies={"tunza_csrf": "stale-nonce"},
        headers={CSRF_HEADER: token},
    ):
        assert csrf_token_is_valid(token) is False
    with _context(
        app,
        path="/api/v1/facilities",
        cookies={"tunza_session": "different-session-token"},
        headers={CSRF_HEADER: token},
    ):
        assert csrf_token_is_valid(token) is False


def test_tampered_token_fails_signature_check(app):
    with app.app_context():
        token, nonce = issue_pre_auth_csrf()
    tampered = token[:-2] + ("AA" if token[-2:] != "AA" else "BB")
    with _context(
        app,
        path="/api/v1/auth/login",
        cookies={"tunza_csrf": nonce},
        headers={CSRF_HEADER: tampered},
    ):
        assert csrf_token_is_valid(tampered) is False
    with _context(app, path="/api/v1/auth/login", cookies={"tunza_csrf": nonce}):
        assert csrf_token_is_valid(None) is False
