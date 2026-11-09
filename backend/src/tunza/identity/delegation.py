"""Delegation allowlists that bound role assignment and prevent privilege escalation."""

from __future__ import annotations

from tunza.identity.models import Role

# Facility administrators may bind only facility-scoped care/admin roles.
FACILITY_DELEGATABLE_ROLE_CODES = frozenset({"VHT", "HCW", "FA"})

# Platform administrators may not self-grant clinical or clinical-approval roles.
PA_DELEGATABLE_ROLE_CODES = frozenset({"FA", "AUTHOR", "ANALYST", "PA"})


def may_delegate_role(grantor_role_codes: set[str], role: Role) -> bool:
    """Whether the grantor's active roles permit assigning the target role."""
    if "FA" in grantor_role_codes and role.scope_kind == "facility":
        if role.code in FACILITY_DELEGATABLE_ROLE_CODES:
            return True
    if "PA" in grantor_role_codes and role.code in PA_DELEGATABLE_ROLE_CODES:
        return role.code in PA_DELEGATABLE_ROLE_CODES
    return False


def may_create_binding_scope(grantor_role_codes: set[str], role: Role, scope_kind: str) -> bool:
    """Binding scope must match the role definition and fall inside the grantor allowlist."""
    if scope_kind != role.scope_kind:
        return False
    return may_delegate_role(grantor_role_codes, role)


def may_revoke_binding(grantor_role_codes: set[str], role: Role, binding_scope_kind: str) -> bool:
    """Revocation uses the same allowlists as grant, including protected roles."""
    if binding_scope_kind != role.scope_kind:
        return False
    return may_delegate_role(grantor_role_codes, role)
