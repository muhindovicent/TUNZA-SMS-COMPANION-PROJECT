import { apiFetch } from './client'

export interface AdminAccount {
  id: string
  login_identifier: string
  display_name: string
  status: string
  version: number
}

export interface AdminMembership {
  id: string
  user_id: string
  facility_id: string
  status: string
  starts_at: string
  ends_at: string | null
  version: number
}

export interface AdminRoleBinding {
  id: string
  user_id: string
  role_code: string | null
  role_name: string | null
  scope_kind: string
  facility_id: string | null
  starts_at: string
  ends_at: string | null
  granted_by: string
  version: number
}

export interface ProvisionResult {
  account: AdminAccount
  facility_id: string | null
  activation_token: string
}

export const ADMIN_PERMISSIONS = [
  'account.provision',
  'membership.manage',
  'role.delegate',
] as const

export function hasAdminPermission(permissions: readonly string[]): boolean {
  return ADMIN_PERMISSIONS.some((permission) => permissions.includes(permission))
}

export function listAccounts(signal?: AbortSignal): Promise<AdminAccount[]> {
  return apiFetch<AdminAccount[]>('/users', { signal })
}

export function provisionAccount(
  body: { login_identifier: string; display_name: string; facility_id?: string },
  signal?: AbortSignal,
): Promise<ProvisionResult> {
  return apiFetch<ProvisionResult>('/users', { method: 'POST', body, signal })
}

export function setAccountState(
  userId: string,
  body: { status: 'active' | 'disabled'; reason?: string },
  signal?: AbortSignal,
): Promise<AdminAccount> {
  return apiFetch<AdminAccount>(`/users/${userId}`, { method: 'PATCH', body, signal })
}

export function listMemberships(signal?: AbortSignal): Promise<AdminMembership[]> {
  return apiFetch<AdminMembership[]>('/memberships', { signal })
}

export function createMembership(
  body: { user_id: string; facility_id: string; starts_at: string; ends_at?: string },
  signal?: AbortSignal,
): Promise<AdminMembership> {
  return apiFetch<AdminMembership>('/memberships', { method: 'POST', body, signal })
}

export function endMembership(
  membershipId: string,
  reason: string,
  signal?: AbortSignal,
): Promise<AdminMembership> {
  return apiFetch<AdminMembership>(`/memberships/${membershipId}`, {
    method: 'PATCH',
    body: { status: 'ended', reason },
    signal,
  })
}

export function listRoleBindings(signal?: AbortSignal): Promise<AdminRoleBinding[]> {
  return apiFetch<AdminRoleBinding[]>('/role-bindings', { signal })
}

export function createRoleBinding(
  body: {
    user_id: string
    role_code: string
    scope_kind: string
    facility_id?: string
    starts_at: string
  },
  signal?: AbortSignal,
): Promise<AdminRoleBinding> {
  return apiFetch<AdminRoleBinding>('/role-bindings', { method: 'POST', body, signal })
}

export function revokeRoleBinding(
  bindingId: string,
  reason: string,
  signal?: AbortSignal,
): Promise<AdminRoleBinding> {
  return apiFetch<AdminRoleBinding>(`/role-bindings/${bindingId}/revoke`, {
    method: 'POST',
    body: { reason },
    signal,
  })
}
