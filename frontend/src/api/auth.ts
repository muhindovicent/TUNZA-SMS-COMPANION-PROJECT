import { ApiError, apiFetch } from './client'

export interface StaffFacility {
  id: string
  code: string
  name: string
}

export interface StaffBinding {
  role_code: string | null
  role_name: string | null
  scope_kind: string
  facility_id: string | null
}

export interface StaffIdentity {
  user_id: string
  display_name: string
  state: string
  facilities: StaffFacility[]
  bindings: StaffBinding[]
  permissions: string[]
}

export interface LoginResult {
  state: string
  user_id: string
}

export async function login(
  loginIdentifier: string,
  password: string,
  signal?: AbortSignal,
): Promise<LoginResult> {
  return apiFetch<LoginResult>('/auth/login', {
    method: 'POST',
    body: { login_identifier: loginIdentifier, password },
    signal,
  })
}

export async function loadIdentity(signal?: AbortSignal): Promise<StaffIdentity | null> {
  try {
    return await apiFetch<StaffIdentity>('/auth/me', { signal })
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null
    throw error
  }
}

export async function logout(): Promise<void> {
  await apiFetch<void>('/auth/logout', { method: 'POST' })
}
