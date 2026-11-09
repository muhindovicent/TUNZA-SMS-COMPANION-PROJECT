"""Controlled staff account, membership and role-binding administration endpoints."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from flask import Response, g, jsonify, request
from sqlalchemy import or_, select
from werkzeug.exceptions import abort

from tunza.access import (
    accessible_facility_ids,
    active_membership_facilities,
    facility_scope_allows,
    get_access_context,
    require_permission,
)
from tunza.audit import record_audit
from tunza.extensions import db
from tunza.identity import admin_blueprint
from tunza.identity.delegation import (
    binding_covers_delegation_scope,
    may_delegate_role,
)
from tunza.identity.models import (
    AuthSession,
    Facility,
    FacilityMembership,
    Role,
    RoleBinding,
    User,
)
from tunza.identity.passwords import hash_password
from tunza.identity.sessions import revoke_session

MAX_LOGIN_IDENTIFIER_LENGTH = 254
MAX_DISPLAY_NAME_LENGTH = 200
MAX_REASON_LENGTH = 1000
ACTIVATION_TOKEN_TTL_HOURS = 72
UNKNOWN_FIELD_MESSAGE = "Request validation failed."


def _error(code: str, message: str, status: int) -> tuple[Response, int]:
    return (
        jsonify(error={"code": code, "message": message, "request_id": g.request_id}),
        status,
    )


def _account_projection(user: User) -> dict[str, object]:
    return {
        "id": str(user.id),
        "login_identifier": user.login_identifier,
        "display_name": user.display_name,
        "status": user.status,
        "version": user.version,
    }


def _membership_projection(membership: FacilityMembership) -> dict[str, object]:
    return {
        "id": str(membership.id),
        "user_id": str(membership.user_id),
        "facility_id": str(membership.facility_id),
        "status": membership.status,
        "starts_at": membership.starts_at.isoformat(),
        "ends_at": membership.ends_at.isoformat() if membership.ends_at else None,
        "version": membership.version,
    }


def _binding_projection(binding: RoleBinding, role: Role | None) -> dict[str, object]:
    return {
        "id": str(binding.id),
        "user_id": str(binding.user_id),
        "role_code": role.code if role else None,
        "role_name": role.name if role else None,
        "scope_kind": binding.scope_kind,
        "facility_id": str(binding.facility_id) if binding.facility_id else None,
        "starts_at": binding.starts_at.isoformat(),
        "ends_at": binding.ends_at.isoformat() if binding.ends_at else None,
        "granted_by": str(binding.granted_by),
        "version": binding.version,
    }


def _reject_unknown_fields(payload: dict, allowed: set[str]) -> tuple[Response, int] | None:
    unknown = set(payload) - allowed
    if unknown:
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
    return None


def _require_str(payload: dict, field: str, *, max_length: int) -> str | None:
    value = payload.get(field)
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or len(cleaned) > max_length:
        return None
    return cleaned


def _parse_datetime(value: object, *, required: bool) -> datetime | None | str:
    """Return datetime, None when optional-and-missing, or the string 'invalid'."""
    if value is None:
        return None if not required else "invalid"
    if not isinstance(value, str):
        return "invalid"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return "invalid"
    if parsed.tzinfo is None:
        return "invalid"
    return parsed.astimezone(UTC)


def _holds_platform_permission(context, permission: str) -> bool:
    return any(
        grant.binding.scope_kind == "platform"
        and grant.binding.facility_id is None
        and permission in grant.permissions
        for grant in context.grants
    )


def _eligible_delegation_binding(
    context,
    *,
    target_scope_kind: str,
    target_facility_id: UUID | None,
    target_role: Role,
) -> RoleBinding | None:
    """Find one active binding that grants role.delegate authority over the target.

    The same binding must provide both the permission and a role code inside the
    delegation allowlist for ``target_role``. FA and PA grants from unrelated
    bindings are never combined. Facility-level authority additionally requires an
    active membership at the target facility for facility-scoped bindings.
    """
    member_facility_ids = active_membership_facilities(context)
    role_ids = [grant.binding.role_id for grant in context.grants]
    role_codes: dict[UUID, str] = {}
    if role_ids:
        for role_id, code in db.session.execute(
            select(Role.id, Role.code).where(Role.id.in_(role_ids))
        ):
            role_codes[role_id] = code
    for grant in context.grants:
        if "role.delegate" not in grant.permissions:
            continue
        binding = grant.binding
        role_code = role_codes.get(binding.role_id)
        if role_code is None or not may_delegate_role({role_code}, target_role):
            continue
        if not binding_covers_delegation_scope(
            binding,
            target_scope_kind=target_scope_kind,
            target_facility_id=target_facility_id,
            member_facility_ids=member_facility_ids,
        ):
            continue
        return binding
    return None


def _delegation_scope_covered(
    context,
    *,
    target_scope_kind: str,
    target_facility_id: UUID | None,
) -> bool:
    """Whether any role.delegate binding covers the target scope (ignoring allowlists)."""
    member_facility_ids = active_membership_facilities(context)
    for grant in context.grants:
        if "role.delegate" not in grant.permissions:
            continue
        if binding_covers_delegation_scope(
            grant.binding,
            target_scope_kind=target_scope_kind,
            target_facility_id=target_facility_id,
            member_facility_ids=member_facility_ids,
        ):
            return True
    return False


def _audit_admin(
    *,
    permission: str,
    action: str,
    resource_type: str,
    outcome: str,
    resource_id: UUID | None = None,
    facility_id: UUID | None = None,
    metadata: dict | None = None,
    actor_id: UUID | None = None,
) -> None:
    record_audit(
        permission=permission,
        action=action,
        resource_type=resource_type,
        outcome=outcome,
        request_id=g.request_id,
        actor_id=actor_id,
        facility_id=facility_id,
        resource_id=resource_id,
        metadata=metadata or {},
    )


def _hash_activation_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def _issue_activation_token(user: User) -> str:
    token = secrets.token_urlsafe(32)
    user.activation_token_hash = _hash_activation_token(token)
    user.activation_expires_at = datetime.now(UTC) + timedelta(hours=ACTIVATION_TOKEN_TTL_HOURS)
    return token


@admin_blueprint.post("/users")
@require_permission("account.provision")
def create_account() -> tuple[Response, int]:
    context = get_access_context()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error("validation_failed", "A JSON request body is required.", 422)
    rejected = _reject_unknown_fields(payload, {"login_identifier", "display_name", "facility_id"})
    if rejected:
        return rejected

    login = _require_str(payload, "login_identifier", max_length=MAX_LOGIN_IDENTIFIER_LENGTH)
    display = _require_str(payload, "display_name", max_length=MAX_DISPLAY_NAME_LENGTH)
    if login is None or display is None:
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
    login = login.lower()
    if "@" in login or " " in login:
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)

    facility_id: UUID | None = None
    if payload.get("facility_id") is not None:
        raw_facility = payload.get("facility_id")
        if not isinstance(raw_facility, str):
            return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
        try:
            facility_id = UUID(raw_facility)
        except ValueError:
            return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
        if not facility_scope_allows(context, facility_id, "account.provision"):
            _audit_admin(
                permission="account.provision",
                action="account.provision",
                resource_type="user",
                outcome="denied",
                facility_id=facility_id,
                metadata={"reason": "facility_out_of_scope"},
                actor_id=context.user.id,
            )
            db.session.commit()
            abort(404)

    existing = db.session.scalar(select(User).where(User.login_identifier == login))
    if existing is not None:
        return _error("conflict", "That login identifier is already in use.", 409)

    user = User(
        login_identifier=login,
        display_name=display,
        password_hash=hash_password(secrets.token_urlsafe(24)),
        status="invited",
    )
    activation_token = _issue_activation_token(user)
    db.session.add(user)
    db.session.flush()

    _audit_admin(
        permission="account.provision",
        action="account.provision",
        resource_type="user",
        outcome="success",
        resource_id=user.id,
        facility_id=facility_id,
        metadata={"state": "invited", "login_identifier": login},
        actor_id=context.user.id,
    )
    db.session.commit()

    body = {
        "data": {
            "account": _account_projection(user),
            "facility_id": str(facility_id) if facility_id else None,
            "activation_token": activation_token,
        }
    }
    response = jsonify(body)
    response.status_code = 201
    response.headers["Location"] = f"/api/v1/users/{user.id}"
    return response, 201


@admin_blueprint.get("/users")
@require_permission("account.provision")
def list_accounts() -> Response:
    context = get_access_context()
    if _holds_platform_permission(context, "account.provision"):
        users = list(db.session.scalars(select(User).order_by(User.login_identifier)).all())
        return jsonify(data=[_account_projection(user) for user in users])

    facility_ids = accessible_facility_ids(context, "account.provision")
    if not facility_ids:
        return jsonify(data=[])

    user_ids = db.session.scalars(
        select(FacilityMembership.user_id).where(
            FacilityMembership.facility_id.in_(facility_ids),
            FacilityMembership.status == "active",
        )
    ).all()
    if not user_ids:
        return jsonify(data=[])
    users = list(
        db.session.scalars(
            select(User).where(User.id.in_(set(user_ids))).order_by(User.login_identifier)
        ).all()
    )
    return jsonify(data=[_account_projection(user) for user in users])


@admin_blueprint.patch("/users/<uuid:user_id>")
@require_permission("account.provision")
def update_account_state(user_id: UUID) -> Response:
    context = get_access_context()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error("validation_failed", "A JSON request body is required.", 422)
    rejected = _reject_unknown_fields(payload, {"status", "reason"})
    if rejected:
        return rejected

    status = payload.get("status")
    if status not in {"active", "disabled"}:
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
    reason = payload.get("reason")
    if reason is not None:
        if not isinstance(reason, str) or not reason.strip() or len(reason) > MAX_REASON_LENGTH:
            return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
        reason = reason.strip()
    if status == "disabled" and not reason:
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)

    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    # Scope: target must be manageable via membership at an account.provision facility,
    # or the grantor must hold platform-wide account.provision.
    platform_wide = _holds_platform_permission(context, "account.provision")
    if not platform_wide:
        managed = accessible_facility_ids(context, "account.provision")
        now = datetime.now(UTC)
        if not managed:
            target_memberships: list[FacilityMembership] = []
        else:
            target_memberships = list(
                db.session.scalars(
                    select(FacilityMembership).where(
                        FacilityMembership.user_id == user.id,
                        FacilityMembership.status == "active",
                    )
                ).all()
            )
        member_of_managed = next(
            (item for item in target_memberships if item.facility_id in managed),
            None,
        )
        if member_of_managed is None:
            _audit_admin(
                permission="account.provision",
                action="account.state",
                resource_type="user",
                outcome="denied",
                resource_id=user.id,
                metadata={"reason": "user_out_of_scope"},
                actor_id=context.user.id,
            )
            db.session.commit()
            abort(404)

        # Shared or globally scoped staff cannot be managed by a facility-only admin:
        # deny when the target also reaches facilities outside the admin's authority,
        # or holds any active platform/programme binding (global staff account).
        beyond_managed = any(item.facility_id not in managed for item in target_memberships)
        global_binding = db.session.scalar(
            select(RoleBinding.id).where(
                RoleBinding.user_id == user.id,
                RoleBinding.scope_kind.in_(("platform", "programme")),
                RoleBinding.starts_at <= now,
                or_(RoleBinding.ends_at.is_(None), RoleBinding.ends_at > now),
            )
        )
        if beyond_managed or global_binding is not None:
            _audit_admin(
                permission="account.provision",
                action="account.state",
                resource_type="user",
                outcome="denied",
                resource_id=user.id,
                metadata={"reason": "shared_or_global_account"},
                actor_id=context.user.id,
            )
            db.session.commit()
            return _error(
                "forbidden",
                "That account has access beyond your administrative authority.",
                403,
            )

    if user.id == context.user.id and status == "disabled":
        _audit_admin(
            permission="account.provision",
            action="account.state",
            resource_type="user",
            outcome="denied",
            resource_id=user.id,
            metadata={"reason": "self_disable"},
            actor_id=context.user.id,
        )
        db.session.commit()
        return _error("forbidden", "You cannot disable your own account.", 403)

    if status == "disabled" and user.status == "invited":
        return _error("conflict", "Invited accounts are activated with an activation token.", 409)
    if status == "active" and user.status == "invited":
        return _error("conflict", "Invited accounts are activated with an activation token.", 409)

    previous = user.status
    user.status = status
    if status == "disabled":
        user.security_version += 1
        user.activation_token_hash = None
        user.activation_expires_at = None
        sessions = db.session.scalars(
            select(AuthSession).where(
                AuthSession.user_id == user.id,
                AuthSession.state == "active",
            )
        ).all()
        for record in sessions:
            revoke_session(record)

    _audit_admin(
        permission="account.provision",
        action="account.state",
        resource_type="user",
        outcome="success",
        resource_id=user.id,
        metadata={"state": f"{previous}->{status}", "reason": reason},
        actor_id=context.user.id,
    )
    db.session.commit()
    return jsonify(data=_account_projection(user))


@admin_blueprint.post("/memberships")
@require_permission("membership.manage")
def create_membership() -> tuple[Response, int]:
    context = get_access_context()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error("validation_failed", "A JSON request body is required.", 422)
    rejected = _reject_unknown_fields(payload, {"user_id", "facility_id", "starts_at", "ends_at"})
    if rejected:
        return rejected

    user_raw = payload.get("user_id")
    facility_raw = payload.get("facility_id")
    if not isinstance(user_raw, str) or not isinstance(facility_raw, str):
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
    try:
        user_id = UUID(user_raw)
        facility_id = UUID(facility_raw)
    except ValueError:
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)

    starts_at = _parse_datetime(payload.get("starts_at"), required=True)
    if starts_at == "invalid" or starts_at is None:
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
    ends_at = _parse_datetime(payload.get("ends_at"), required=False)
    if ends_at == "invalid":
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
    if ends_at is not None and ends_at <= starts_at:
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)

    if not facility_scope_allows(context, facility_id, "membership.manage"):
        _audit_admin(
            permission="membership.manage",
            action="membership.create",
            resource_type="facility_membership",
            outcome="denied",
            facility_id=facility_id,
            metadata={"reason": "facility_out_of_scope"},
            actor_id=context.user.id,
        )
        db.session.commit()
        abort(404)

    user = db.session.get(User, user_id)
    facility = db.session.get(Facility, facility_id)
    if user is None or facility is None:
        abort(404)

    existing = db.session.scalar(
        select(FacilityMembership).where(
            FacilityMembership.user_id == user_id,
            FacilityMembership.facility_id == facility_id,
        )
    )
    if existing is not None:
        return _error("conflict", "That membership already exists.", 409)

    membership = FacilityMembership(
        user_id=user_id,
        facility_id=facility_id,
        status="active",
        starts_at=starts_at,
        ends_at=ends_at,
    )
    db.session.add(membership)
    db.session.flush()

    _audit_admin(
        permission="membership.manage",
        action="membership.create",
        resource_type="facility_membership",
        outcome="success",
        resource_id=membership.id,
        facility_id=facility_id,
        metadata={"state": "created"},
        actor_id=context.user.id,
    )
    db.session.commit()
    response = jsonify(data=_membership_projection(membership))
    response.status_code = 201
    response.headers["Location"] = f"/api/v1/memberships/{membership.id}"
    return response, 201


@admin_blueprint.get("/memberships")
@require_permission("membership.manage")
def list_memberships() -> Response:
    context = get_access_context()
    facility_ids = accessible_facility_ids(context, "membership.manage")
    if not facility_ids:
        return jsonify(data=[])
    query = (
        select(FacilityMembership)
        .where(FacilityMembership.facility_id.in_(facility_ids))
        .order_by(FacilityMembership.created_at.desc(), FacilityMembership.id)
    )
    facility_filter = request.args.get("facility_id")
    if facility_filter:
        try:
            query = query.where(FacilityMembership.facility_id == UUID(facility_filter))
        except ValueError:
            return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
    memberships = list(db.session.scalars(query).all())
    return jsonify(data=[_membership_projection(item) for item in memberships])


@admin_blueprint.patch("/memberships/<uuid:membership_id>")
@require_permission("membership.manage")
def update_membership(membership_id: UUID) -> Response:
    context = get_access_context()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error("validation_failed", "A JSON request body is required.", 422)
    rejected = _reject_unknown_fields(payload, {"status", "ends_at", "reason"})
    if rejected:
        return rejected

    status = payload.get("status")
    if status not in {"active", "ended"}:
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
    reason = payload.get("reason")
    if reason is not None:
        if not isinstance(reason, str) or not reason.strip() or len(reason) > MAX_REASON_LENGTH:
            return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
        reason = reason.strip()
    if status == "ended" and not reason:
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)

    ends_at = _parse_datetime(payload.get("ends_at"), required=False)
    if ends_at == "invalid":
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)

    membership = db.session.get(FacilityMembership, membership_id)
    if membership is None:
        abort(404)
    if not facility_scope_allows(context, membership.facility_id, "membership.manage"):
        _audit_admin(
            permission="membership.manage",
            action="membership.update",
            resource_type="facility_membership",
            outcome="denied",
            resource_id=membership.id,
            facility_id=membership.facility_id,
            metadata={"reason": "facility_out_of_scope"},
            actor_id=context.user.id,
        )
        db.session.commit()
        abort(404)

    previous = membership.status
    membership.status = status
    if status == "ended":
        candidate = ends_at or datetime.now(UTC)
        if candidate <= membership.starts_at:
            candidate = membership.starts_at + timedelta(seconds=1)
        membership.ends_at = candidate

    _audit_admin(
        permission="membership.manage",
        action="membership.update",
        resource_type="facility_membership",
        outcome="success",
        resource_id=membership.id,
        facility_id=membership.facility_id,
        metadata={"reason": reason, "state": f"{previous}->{status}"},
        actor_id=context.user.id,
    )
    db.session.commit()
    return jsonify(data=_membership_projection(membership))


@admin_blueprint.post("/role-bindings")
@require_permission("role.delegate")
def create_role_binding() -> tuple[Response, int]:
    context = get_access_context()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error("validation_failed", "A JSON request body is required.", 422)
    rejected = _reject_unknown_fields(
        payload,
        {"user_id", "role_code", "scope_kind", "facility_id", "starts_at", "ends_at"},
    )
    if rejected:
        return rejected

    user_raw = payload.get("user_id")
    role_code = payload.get("role_code")
    scope_kind = payload.get("scope_kind")
    if (
        not isinstance(user_raw, str)
        or not isinstance(role_code, str)
        or not isinstance(scope_kind, str)
    ):
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
    try:
        user_id = UUID(user_raw)
    except ValueError:
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)

    starts_at = _parse_datetime(payload.get("starts_at"), required=True)
    if starts_at == "invalid" or starts_at is None:
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
    ends_at = _parse_datetime(payload.get("ends_at"), required=False)
    if ends_at == "invalid":
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
    if ends_at is not None and ends_at <= starts_at:
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)

    facility_id: UUID | None = None
    if payload.get("facility_id") is not None:
        raw_facility = payload.get("facility_id")
        if not isinstance(raw_facility, str):
            return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
        try:
            facility_id = UUID(raw_facility)
        except ValueError:
            return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)

    target_user = db.session.get(User, user_id)
    role = db.session.scalar(select(Role).where(Role.code == role_code.strip()))
    if target_user is None or role is None:
        abort(404)

    # No self-elevation: never create bindings for the acting administrator.
    if user_id == context.user.id:
        _audit_admin(
            permission="role.delegate",
            action="role.delegate",
            resource_type="role_binding",
            outcome="denied",
            metadata={"reason": "self_grant"},
            actor_id=context.user.id,
        )
        db.session.commit()
        return _error("forbidden", "You cannot assign a role to yourself.", 403)

    if scope_kind != role.scope_kind:
        _audit_admin(
            permission="role.delegate",
            action="role.delegate",
            resource_type="role_binding",
            outcome="denied",
            metadata={"reason": "delegation_not_allowed", "role_code": role.code},
            actor_id=context.user.id,
        )
        db.session.commit()
        return _error("forbidden", "That role is outside your delegation scope.", 403)

    facility_id_for_check: UUID | None = None
    if scope_kind == "facility":
        if facility_id is None:
            return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
        facility_id_for_check = facility_id

    if not _delegation_scope_covered(
        context,
        target_scope_kind=scope_kind,
        target_facility_id=facility_id_for_check,
    ):
        if scope_kind == "facility":
            _audit_admin(
                permission="role.delegate",
                action="role.delegate",
                resource_type="role_binding",
                outcome="denied",
                facility_id=facility_id,
                metadata={"reason": "facility_out_of_scope"},
                actor_id=context.user.id,
            )
            db.session.commit()
            abort(404)
        _audit_admin(
            permission="role.delegate",
            action="role.delegate",
            resource_type="role_binding",
            outcome="denied",
            metadata={"reason": "platform_scope_required", "role_code": role.code},
            actor_id=context.user.id,
        )
        db.session.commit()
        return _error("forbidden", "That role is outside your delegation scope.", 403)

    # One eligible binding must supply both role.delegate and allowlisted authority.
    eligible = _eligible_delegation_binding(
        context,
        target_scope_kind=scope_kind,
        target_facility_id=facility_id_for_check,
        target_role=role,
    )
    if eligible is None:
        _audit_admin(
            permission="role.delegate",
            action="role.delegate",
            resource_type="role_binding",
            outcome="denied",
            facility_id=facility_id,
            metadata={"reason": "delegation_not_allowed", "role_code": role.code},
            actor_id=context.user.id,
        )
        db.session.commit()
        return _error("forbidden", "That role is outside your delegation scope.", 403)

    if scope_kind == "facility" and facility_id is not None:
        if role.scope_kind == "facility":
            # Require active membership at the target facility for care roles.
            member = db.session.scalar(
                select(FacilityMembership.id).where(
                    FacilityMembership.user_id == user_id,
                    FacilityMembership.facility_id == facility_id,
                    FacilityMembership.status == "active",
                )
            )
            if member is None and role.code != "FA":
                return _error(
                    "conflict",
                    "An active facility membership is required before assigning this role.",
                    409,
                )
    elif facility_id is not None:
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)

    binding = RoleBinding(
        user_id=user_id,
        role_id=role.id,
        facility_id=facility_id if scope_kind == "facility" else None,
        scope_kind=scope_kind,
        starts_at=starts_at,
        ends_at=ends_at,
        granted_by=context.user.id,
    )
    db.session.add(binding)
    db.session.flush()

    _audit_admin(
        permission="role.delegate",
        action="role.delegate",
        resource_type="role_binding",
        outcome="success",
        resource_id=binding.id,
        facility_id=facility_id,
        metadata={
            "role_code": role.code,
            "scope_kind": scope_kind,
            "state": "granted",
        },
        actor_id=context.user.id,
    )
    db.session.commit()
    response = jsonify(data=_binding_projection(binding, role))
    response.status_code = 201
    response.headers["Location"] = f"/api/v1/role-bindings/{binding.id}"
    return response, 201


@admin_blueprint.get("/role-bindings")
@require_permission("role.delegate")
def list_role_bindings() -> Response:
    context = get_access_context()
    query = select(RoleBinding).order_by(RoleBinding.created_at.desc(), RoleBinding.id)
    if not _holds_platform_permission(context, "role.delegate"):
        facility_ids = accessible_facility_ids(context, "role.delegate")
        if not facility_ids:
            return jsonify(data=[])
        query = query.where(RoleBinding.facility_id.in_(facility_ids))
    bindings = list(db.session.scalars(query).all())
    role_ids = {binding.role_id for binding in bindings}
    roles = (
        {
            role.id: role
            for role in db.session.scalars(select(Role).where(Role.id.in_(role_ids))).all()
        }
        if role_ids
        else {}
    )
    return jsonify(data=[_binding_projection(item, roles.get(item.role_id)) for item in bindings])


@admin_blueprint.post("/role-bindings/<uuid:binding_id>/revoke")
@require_permission("role.delegate")
def revoke_role_binding(binding_id: UUID) -> Response:
    context = get_access_context()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error("validation_failed", "A JSON request body is required.", 422)
    rejected = _reject_unknown_fields(payload, {"reason"})
    if rejected:
        return rejected
    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > MAX_REASON_LENGTH:
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
    reason = reason.strip()

    binding = db.session.get(RoleBinding, binding_id)
    if binding is None:
        abort(404)

    role = db.session.get(Role, binding.role_id)
    if role is None:
        abort(404)

    if binding.scope_kind != role.scope_kind:
        abort(404)

    facility_id_for_check = binding.facility_id if binding.scope_kind == "facility" else None
    if not _delegation_scope_covered(
        context,
        target_scope_kind=binding.scope_kind,
        target_facility_id=facility_id_for_check,
    ):
        if binding.scope_kind == "facility":
            _audit_admin(
                permission="role.delegate",
                action="role.revoke",
                resource_type="role_binding",
                outcome="denied",
                resource_id=binding.id,
                facility_id=binding.facility_id,
                metadata={"reason": "facility_out_of_scope"},
                actor_id=context.user.id,
            )
            db.session.commit()
            abort(404)
        _audit_admin(
            permission="role.delegate",
            action="role.revoke",
            resource_type="role_binding",
            outcome="denied",
            resource_id=binding.id,
            metadata={"reason": "platform_scope_required"},
            actor_id=context.user.id,
        )
        db.session.commit()
        abort(404)

    # Revocation requires the same single eligible binding as grant, including
    # protected clinical and platform roles (PA cannot revoke VHT/HCW/REVIEWER).
    eligible = _eligible_delegation_binding(
        context,
        target_scope_kind=binding.scope_kind,
        target_facility_id=facility_id_for_check,
        target_role=role,
    )
    if eligible is None:
        _audit_admin(
            permission="role.delegate",
            action="role.revoke",
            resource_type="role_binding",
            outcome="denied",
            resource_id=binding.id,
            facility_id=binding.facility_id,
            metadata={"reason": "delegation_not_allowed", "role_code": role.code},
            actor_id=context.user.id,
        )
        db.session.commit()
        return _error("forbidden", "That role is outside your delegation scope.", 403)

    if binding.ends_at is not None and binding.ends_at <= datetime.now(UTC):
        return _error("conflict", "That role binding is already revoked.", 409)

    now = datetime.now(UTC)
    if binding.starts_at >= now:
        # Future-dated binding: close the interval at now while keeping ends_at >
        # starts_at, so the grant can never become active after revocation.
        binding.ends_at = now
        binding.starts_at = now - timedelta(seconds=1)
    else:
        binding.ends_at = now

    _audit_admin(
        permission="role.delegate",
        action="role.revoke",
        resource_type="role_binding",
        outcome="success",
        resource_id=binding.id,
        facility_id=binding.facility_id,
        metadata={"reason": reason, "role_code": role.code},
        actor_id=context.user.id,
    )
    db.session.commit()
    return jsonify(data=_binding_projection(binding, role))
