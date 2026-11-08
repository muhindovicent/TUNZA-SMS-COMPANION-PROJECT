from tunza.identity.sessions import clear_session_cookie, set_session_cookie


def test_session_cookie_is_secure_httponly_and_samesite_lax(app):
    with app.test_request_context("/"):
        response = app.response_class()
        set_session_cookie(response, "synthetic-session-token")
        header = response.headers["Set-Cookie"]
    assert "tunza_session=synthetic-session-token" in header
    assert "HttpOnly" in header
    assert "Secure" in header
    assert "SameSite=Lax" in header
    assert "Path=/" in header


def test_clearing_session_cookie_keeps_protective_flags(app):
    with app.test_request_context("/"):
        response = app.response_class()
        clear_session_cookie(response)
        header = response.headers["Set-Cookie"]
    assert "tunza_session=" in header
    assert "HttpOnly" in header
    assert "Secure" in header
    assert "SameSite=Lax" in header
