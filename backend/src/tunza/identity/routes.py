"""Staff login, logout, session introspection and scoped facility endpoints."""

from __future__ import annotations

from flask import Response, current_app, g, jsonify, request
from sqlalchemy import select
from werkzeug.exceptions import abort

from tunza.access import (
    accessible_facility_ids,
    facility_scope_allows,
    get_access_context,
    require_permission,
)
from tunza.audit import record_audit
from tunza.csrf import issue_csrf_token, issue_pre_auth_csrf
from tunza.extensions import db
from tunza.identity import auth_blueprint, facilities_blueprint
from tunza.identity.models import Facility, Role, User
from tunza.identity.passwords import discard_password_verification, verify_password
from tunza.identity.sessions import (
    clear_session_cookie,
    create_session,
    get_current_session,
    revoke_session,
    set_session_cookie,
)
from tunza.services.rate_limit import SlidingWindowRateLimiter

GENERIC_LOGIN_FAILURE = "Sign-in details could not be verified."
SERVICE_ACTOR = "tunza-api"
MAX_LOGIN_IDENTIFIER_LENGTH = 254
MAX_PASSWORD_LENGTH = 1024


def _error(code: str, message: str, status: int) -> tuple[Response, int]:
    return (
        jsonify(
            error={
                "code": code,
                "message": message,
                "request_id": g.request_id,
            }
        ),
        status,
    )


def _facility_projection(facility: Facility) -> dict[str, object]:
    return {
        "id": str(facility.id),
        "code": facility.code,
        "name": facility.name,
        "timezone": facility.timezone,
        "active": facility.active,
        "version": facility.version,
    }


def _limiter() -> SlidingWindowRateLimiter:
    return current_app.extensions["tunza.login_rate_limiter"]


def _login_rate_keys(identifier: str) -> tuple[str, str]:
    address = request.remote_addr or "unknown"
    return f"login:ip:{address}", f"login:id:{identifier}"


@auth_blueprint.get("/csrf")
def csrf() -> Response:
    session_token = request.cookies.get(current_app.config["SESSION_COOKIE_NAME"])
    if session_token:
        return jsonify(data={"csrf_token": issue_csrf_token(session_token)})
    token, nonce = issue_pre_auth_csrf()
    response = jsonify(data={"csrf_token": token})
    response.set_cookie(
        current_app.config["CSRF_COOKIE_NAME"],
        nonce,
        max_age=current_app.config["CSRF_TOKEN_MAX_AGE_SECONDS"],
        httponly=False,
        secure=current_app.config["SESSION_COOKIE_SECURE"],
        samesite="Lax",
        path="/",
    )
    return response


@auth_blueprint.post("/login")
def login() -> tuple[Response, int]:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error("validation_failed", "A JSON request body is required.", 422)
    identifier_value = payload.get("login_identifier")
    password_value = payload.get("password")
    if not isinstance(identifier_value, str) or not isinstance(password_value, str):
        return _error("validation_failed", "Request validation failed.", 422)
    identifier = identifier_value.strip().lower()
    password = password_value
    if not identifier or len(identifier) > MAX_LOGIN_IDENTIFIER_LENGTH:
        return _error("validation_failed", "Request validation failed.", 422)
    if not password or len(password) > MAX_PASSWORD_LENGTH:
        return _error("validation_failed", "Request validation failed.", 422)

    ip_key, identifier_key = _login_rate_keys(identifier)
    for key in (ip_key, identifier_key):
        retry_after = _limiter().retry_after(key)
        if retry_after is not None:
            response, status = _error(
                "rate_limited", "Too many sign-in attempts. Try again shortly.", 429
            )
            response.headers["Retry-After"] = str(retry_after)
            return response, status

    user = db.session.scalar(select(User).where(User.login_identifier == identifier))
    password_ok = False
    if user is None:
        discard_password_verification(password)
    else:
        password_ok = verify_password(user.password_hash, password)
    allowed = user is not None and user.status == "active" and password_ok

    if not allowed:
        _limiter().record_failure(ip_key)
        _limiter().record_failure(identifier_key)
        reason = "account_inactive" if user is not None and password_ok else "invalid_credentials"
        record_audit(
            permission="auth",
            action="login",
            resource_type="session",
            outcome="denied",
            request_id=g.request_id,
            actor_id=user.id if user is not None else None,
            service_actor=SERVICE_ACTOR,
            metadata={"reason": reason},
        )
        db.session.commit()
        return _error("invalid_credentials", GENERIC_LOGIN_FAILURE, 401)

    _limiter().reset(ip_key)
    _limiter().reset(identifier_key)
    record, token = create_session(user)
    record_audit(
        permission="auth",
        action="login",
        resource_type="session",
        outcome="success",
        request_id=g.request_id,
        actor_id=user.id,
        service_actor=SERVICE_ACTOR,
        resource_id=record.id,
        metadata={"state": "active"},
    )
    db.session.commit()
    response = jsonify(data={"state": "active", "user_id": str(user.id)})
    set_session_cookie(response, token)
    return response, 200


@auth_blueprint.post("/logout")
def logout() -> Response:
    record, user = get_current_session()
    if record is not None:
        revoke_session(record)
        record_audit(
            permission="auth",
            action="logout",
            resource_type="session",
            outcome="success",
            request_id=g.request_id,
            actor_id=user.id,
            service_actor=SERVICE_ACTOR,
            resource_id=record.id,
        )
        db.session.commit()
    else:
        record_audit(
            permission="auth",
            action="logout",
            resource_type="session",
            outcome="success",
            request_id=g.request_id,
            service_actor=SERVICE_ACTOR,
            metadata={"state": "already_signed_out"},
        )
        db.session.commit()
    response = Response(status=204)
    clear_session_cookie(response)
    return response


@auth_blueprint.get("/me")
def me() -> Response:
    context = get_access_context()
    if context is None:
        abort(401)

    membership_ids = [membership.facility_id for membership in context.memberships]
    facilities: list[Facility] = []
    if membership_ids:
        facilities = list(
            db.session.scalars(
                select(Facility).where(Facility.id.in_(membership_ids)).order_by(Facility.code)
            ).all()
        )
    role_ids = [binding.role_id for binding in context.bindings]
    roles: dict[object, Role] = {}
    if role_ids:
        roles = {
            role.id: role
            for role in db.session.scalars(select(Role).where(Role.id.in_(role_ids))).all()
        }
    bindings = []
    for binding in sorted(context.bindings, key=lambda item: str(item.id)):
        role = roles.get(binding.role_id)
        bindings.append(
            {
                "role_code": role.code if role else None,
                "role_name": role.name if role else None,
                "scope_kind": binding.scope_kind,
                "facility_id": str(binding.facility_id) if binding.facility_id else None,
            }
        )
    return jsonify(
        data={
            "user_id": str(context.user.id),
            "display_name": context.user.display_name,
            "state": "active",
            "facilities": [
                {"id": str(facility.id), "code": facility.code, "name": facility.name}
                for facility in facilities
            ],
            "bindings": bindings,
            "permissions": sorted(context.permissions),
        }
    )


@facilities_blueprint.get("")
@require_permission("facility.read")
def list_facilities() -> Response:
    context = get_access_context()
    facility_ids = accessible_facility_ids(context, "facility.read")
    facilities: list[Facility] = []
    if facility_ids:
        facilities = list(
            db.session.scalars(
                select(Facility)
                .where(Facility.id.in_(facility_ids), Facility.active.is_(True))
                .order_by(Facility.code)
            ).all()
        )
    return jsonify(data=[_facility_projection(facility) for facility in facilities])


@facilities_blueprint.get("/<uuid:facility_id>")
@require_permission("facility.read")
def facility_detail(facility_id) -> Response:
    context = get_access_context()
    if not facility_scope_allows(context, facility_id, "facility.read"):
        abort(404)
    facility = db.session.get(Facility, facility_id)
    if facility is None or not facility.active:
        abort(404)
    return jsonify(data=_facility_projection(facility))
