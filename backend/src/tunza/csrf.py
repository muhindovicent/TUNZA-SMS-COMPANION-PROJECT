"""Signed CSRF tokens bound to the session cookie or a pre-auth browser nonce."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any

from flask import Flask, current_app, request
from itsdangerous import BadSignature, URLSafeTimedSerializer
from werkzeug.exceptions import abort

CSRF_HEADER = "X-CSRF-Token"
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_SERIALIZER_SALT = "tunza.csrf.v1"


def _serializer() -> URLSafeTimedSerializer:
    cached = current_app.extensions.get("tunza.csrf")
    if cached is None:
        cached = URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=_SERIALIZER_SALT)
        current_app.extensions["tunza.csrf"] = cached
    return cached


def _session_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_csrf_token(session_token: str | None) -> str:
    payload: dict[str, Any] = (
        {"s": _session_digest(session_token)} if session_token else {"n": secrets.token_urlsafe(16)}
    )
    return _serializer().dumps(payload)


def issue_pre_auth_csrf() -> tuple[str, str]:
    nonce = secrets.token_urlsafe(16)
    return _serializer().dumps({"n": nonce}), nonce


def csrf_token_is_valid(presented: str | None) -> bool:
    if not presented:
        return False
    session_token = request.cookies.get(current_app.config["SESSION_COOKIE_NAME"])
    if session_token:
        expected: dict[str, Any] = {"s": _session_digest(session_token)}
    else:
        nonce = request.cookies.get(current_app.config["CSRF_COOKIE_NAME"])
        if not nonce:
            return False
        expected = {"n": nonce}
    try:
        actual = _serializer().loads(
            presented, max_age=current_app.config["CSRF_TOKEN_MAX_AGE_SECONDS"]
        )
    except BadSignature:
        return False
    if not isinstance(actual, dict) or set(actual) != set(expected):
        return False
    key = next(iter(expected))
    return hmac.compare_digest(str(actual.get(key)), str(expected.get(key)))


def register_csrf_protection(app: Flask) -> None:
    @app.before_request
    def enforce_csrf() -> None:
        if request.method not in MUTATING_METHODS:
            return
        if request.routing_exception is not None:
            return
        if not request.path.startswith("/api/v1/"):
            return
        if not csrf_token_is_valid(request.headers.get(CSRF_HEADER)):
            abort(403)
