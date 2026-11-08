"""Per-request role/permission context with facility-level isolation checks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import wraps
from uuid import UUID

from flask import g, request
from sqlalchemy import or_, select
from werkzeug.exceptions import abort

from tunza.audit import record_audit
from tunza.extensions import db
from tunza.identity.models import (
    Facility,
    FacilityMembership,
    Permission,
    Role,
    RoleBinding,
    User,
    role_permissions,
)
from tunza.identity.sessions import get_current_session

CONTEXT_G = "tunza_access_context"


@dataclass(frozen=True)
class BindingGrant:
    """Permissions granted by one active role binding, validated against its role."""

    binding: RoleBinding
    permissions: frozenset[str]


@dataclass(frozen=True)
class AccessContext:
    user: User
    memberships: tuple[FacilityMembership, ...]
    grants: tuple[BindingGrant, ...]

    @property
    def bindings(self) -> tuple[RoleBinding, ...]:
        return tuple(grant.binding for grant in self.grants)

    @property
    def permissions(self) -> frozenset[str]:
        codes: set[str] = set()
        for grant in self.grants:
            codes.update(grant.permissions)
        return frozenset(codes)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def load_access_context(user: User) -> AccessContext:
    now = _utcnow()
    memberships = tuple(
        db.session.scalars(
            select(FacilityMembership).where(
                FacilityMembership.user_id == user.id,
                FacilityMembership.status == "active",
                FacilityMembership.starts_at <= now,
                or_(FacilityMembership.ends_at.is_(None), FacilityMembership.ends_at > now),
            )
        ).all()
    )
    binding_rows = tuple(
        db.session.execute(
            select(RoleBinding)
            .join(Role, Role.id == RoleBinding.role_id)
            .where(
                RoleBinding.user_id == user.id,
                RoleBinding.starts_at <= now,
                or_(RoleBinding.ends_at.is_(None), RoleBinding.ends_at > now),
                RoleBinding.scope_kind == Role.scope_kind,
            )
        ).scalars()
    )
    permissions_by_role: dict[UUID, set[str]] = {}
    if binding_rows:
        role_ids = [binding.role_id for binding in binding_rows]
        for role_id, code in db.session.execute(
            select(role_permissions.c.role_id, Permission.code)
            .join(Permission, Permission.id == role_permissions.c.permission_id)
            .where(role_permissions.c.role_id.in_(role_ids))
        ):
            permissions_by_role.setdefault(role_id, set()).add(code)
    grants = tuple(
        BindingGrant(
            binding=binding,
            permissions=frozenset(permissions_by_role.get(binding.role_id, ())),
        )
        for binding in binding_rows
    )
    return AccessContext(user=user, memberships=memberships, grants=grants)


def get_access_context() -> AccessContext | None:
    if hasattr(g, CONTEXT_G):
        return getattr(g, CONTEXT_G)
    _record, user = get_current_session()
    context = load_access_context(user) if user is not None else None
    setattr(g, CONTEXT_G, context)
    return context


def active_membership_facilities(context: AccessContext) -> set[UUID]:
    return {membership.facility_id for membership in context.memberships}


def facility_scope_allows(context: AccessContext, facility_id: UUID, permission: str) -> bool:
    member_facilities = active_membership_facilities(context)
    for grant in context.grants:
        if permission not in grant.permissions:
            continue
        binding = grant.binding
        if binding.scope_kind == "facility" and binding.facility_id == facility_id:
            if facility_id in member_facilities:
                return True
        elif binding.scope_kind == "platform" and binding.facility_id is None:
            return True
    return False


def accessible_facility_ids(context: AccessContext, permission: str) -> set[UUID]:
    member_facilities = active_membership_facilities(context)
    scoped: set[UUID] = set()
    platform_wide = False
    for grant in context.grants:
        if permission not in grant.permissions:
            continue
        binding = grant.binding
        if binding.scope_kind == "facility" and binding.facility_id in member_facilities:
            scoped.add(binding.facility_id)
        elif binding.scope_kind == "platform" and binding.facility_id is None:
            platform_wide = True
    if platform_wide:
        rows = db.session.scalars(select(Facility.id).where(Facility.active.is_(True))).all()
        scoped.update(rows)
    return scoped


def _record_denied_access(context: AccessContext, permission: str) -> None:
    record_audit(
        permission=permission,
        action="access",
        resource_type="endpoint",
        outcome="denied",
        request_id=g.request_id,
        actor_id=context.user.id,
        metadata={"endpoint": (request.endpoint or "unknown")[:64]},
    )
    db.session.commit()


def require_authenticated(view: Callable):
    @wraps(view)
    def wrapper(*args, **kwargs):
        context = get_access_context()
        if context is None:
            abort(401)
        return view(*args, **kwargs)

    return wrapper


def require_permission(permission: str):
    def decorator(view: Callable):
        @wraps(view)
        def wrapper(*args, **kwargs):
            context = get_access_context()
            if context is None:
                abort(401)
            if permission not in context.permissions:
                _record_denied_access(context, permission)
                abort(403)
            return view(*args, **kwargs)

        return wrapper

    return decorator
