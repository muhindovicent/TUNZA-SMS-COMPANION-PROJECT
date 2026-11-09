"""Delegation allowlists that bound role assignment and prevent privilege escalation."""

from __future__ import annotations

from uuid import UUID

from tunza.identity.models import Role, RoleBinding

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


def binding_covers_delegation_scope(
    binding: RoleBinding,
    *,
    target_scope_kind: str,
    target_facility_id: UUID | None,
    member_facility_ids: set[UUID],
) -> bool:
    """Whether one active binding's scope covers the requested grant/revoke target."""
    if target_scope_kind == "facility":
        if target_facility_id is None:
            return False
        if binding.scope_kind == "platform" and binding.facility_id is None:
            return True
        if (
            binding.scope_kind == "facility"
            and binding.facility_id == target_facility_id
            and target_facility_id in member_facility_ids
        ):
            return True
        return False
    # Platform and programme targets require a platform-scope binding.
    return binding.scope_kind == "platform" and binding.facility_id is None
