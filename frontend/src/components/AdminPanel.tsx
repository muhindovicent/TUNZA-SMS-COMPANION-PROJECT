import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import {
  createRoleBinding,
  endMembership,
  listAccounts,
  listMemberships,
  listRoleBindings,
  provisionAccount,
  revokeRoleBinding,
  setAccountState,
} from '../api/admin'
import type {
  AdminAccount,
  AdminMembership,
  AdminRoleBinding,
  ProvisionResult,
} from '../api/admin'
import { ApiError } from '../api/client'
import type { StaffIdentity } from '../api/auth'

interface AdminPanelProps {
  identity: StaffIdentity
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message
  return 'The request could not be completed. Try again.'
}

function isoNow(): string {
  return new Date().toISOString()
}

export default function AdminPanel({ identity }: AdminPanelProps) {
  const permissions = identity.permissions
  const canProvision = permissions.includes('account.provision')
  const canManageMemberships = permissions.includes('membership.manage')
  const canDelegate = permissions.includes('role.delegate')
  const facilityOptions = identity.facilities

  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [accounts, setAccounts] = useState<AdminAccount[]>([])
  const [memberships, setMemberships] = useState<AdminMembership[]>([])
  const [bindings, setBindings] = useState<AdminRoleBinding[]>([])

  const [loginIdentifier, setLoginIdentifier] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [provisionFacilityId, setProvisionFacilityId] = useState(
    facilityOptions[0]?.id ?? '',
  )
  const [lastToken, setLastToken] = useState<string | null>(null)

  const [bindingUserId, setBindingUserId] = useState('')
  const [bindingRoleCode, setBindingRoleCode] = useState('VHT')
  const [bindingFacilityId, setBindingFacilityId] = useState(facilityOptions[0]?.id ?? '')
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const results = await Promise.all([
        canProvision ? listAccounts(signal) : Promise.resolve([]),
        canManageMemberships ? listMemberships(signal) : Promise.resolve([]),
        canDelegate ? listRoleBindings(signal) : Promise.resolve([]),
      ])
      if (signal?.aborted) return
      setAccounts(results[0])
      setMemberships(results[1])
      setBindings(results[2])
      setError(null)
    } catch (loadError) {
      if (signal?.aborted) return
      setError(errorMessage(loadError))
    }
  }, [canProvision, canManageMemberships, canDelegate])

  useEffect(() => {
    const controller = new AbortController()
    void Promise.resolve().then(() => refresh(controller.signal))
    return () => controller.abort()
  }, [refresh])

  function accountLabel(userId: string): string {
    const match = accounts.find((account) => account.id === userId)
    if (match) return match.login_identifier
    if (userId === identity.user_id) return identity.display_name
    return userId.slice(0, 8)
  }

  async function run(task: () => Promise<void>, success: string) {
    if (busy) return
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      await task()
      setNotice(success)
      await refresh()
    } catch (actionError) {
      setError(errorMessage(actionError))
    } finally {
      setBusy(false)
    }
  }

  function handleProvision(event: FormEvent) {
    event.preventDefault()
    void run(async () => {
      const result: ProvisionResult = await provisionAccount({
        login_identifier: loginIdentifier.trim(),
        display_name: displayName.trim(),
        ...(provisionFacilityId ? { facility_id: provisionFacilityId } : {}),
      })
      setLastToken(result.activation_token)
      setLoginIdentifier('')
      setDisplayName('')
    }, 'Account provisioned. Share the activation token out-of-band.')
  }

  function handleDisable(account: AdminAccount) {
    void run(async () => {
      await setAccountState(account.id, {
        status: 'disabled',
        reason: 'synthetic deactivation',
      })
    }, 'Account disabled and sessions revoked.')
  }

  function handleEnable(account: AdminAccount) {
    void run(async () => {
      await setAccountState(account.id, { status: 'active' })
    }, 'Account reactivated.')
  }

  function handleEndMembership(membership: AdminMembership) {
    void run(async () => {
      await endMembership(membership.id, 'synthetic membership end')
    }, 'Membership ended.')
  }

  function handleRevokeBinding(binding: AdminRoleBinding) {
    void run(async () => {
      await revokeRoleBinding(binding.id, 'synthetic role revoke')
    }, 'Role binding revoked.')
  }

  function handleCreateBinding(event: FormEvent) {
    event.preventDefault()
    const facilityScoped = bindingRoleCode === 'VHT' || bindingRoleCode === 'HCW' || bindingRoleCode === 'FA'
    const scopeKind = bindingRoleCode === 'PA' ? 'platform' : facilityScoped ? 'facility' : 'programme'
    void run(async () => {
      await createRoleBinding({
        user_id: bindingUserId.trim(),
        role_code: bindingRoleCode,
        scope_kind: scopeKind,
        ...(facilityScoped ? { facility_id: bindingFacilityId } : {}),
        starts_at: isoNow(),
      })
      setBindingUserId('')
    }, 'Role binding created.')
  }

  return (
    <section className="admin-panel content-width" aria-labelledby="admin-title">
      <div className="section-heading">
        <div>
          <p className="section-kicker">Staff administration</p>
          <h2 id="admin-title">Controlled access administration</h2>
        </div>
        <button type="button" className="secondary-button" onClick={() => void refresh()}>
          Refresh
        </button>
      </div>

      {notice ? (
        <p className="admin-notice" role="status">
          {notice}
        </p>
      ) : null}
      {error ? (
        <p className="auth-error" role="alert">
          {error}
        </p>
      ) : null}

      {canProvision ? (
        <article className="admin-card">
          <h3>Provision account</h3>
          <form onSubmit={handleProvision} className="admin-form">
            <div className="field">
              <label htmlFor="admin-login">Login identifier</label>
              <input
                id="admin-login"
                value={loginIdentifier}
                onChange={(event) => setLoginIdentifier(event.target.value)}
                autoComplete="off"
                required
              />
            </div>
            <div className="field">
              <label htmlFor="admin-display">Display name</label>
              <input
                id="admin-display"
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                required
              />
            </div>
            <div className="field">
              <label htmlFor="admin-facility">Facility (optional)</label>
              <select
                id="admin-facility"
                value={provisionFacilityId}
                onChange={(event) => setProvisionFacilityId(event.target.value)}
              >
                <option value="">Platform scope</option>
                {facilityOptions.map((facility) => (
                  <option key={facility.id} value={facility.id}>
                    {facility.code} — {facility.name}
                  </option>
                ))}
              </select>
            </div>
            <button type="submit" className="primary-button" disabled={busy}>
              {busy ? 'Working…' : 'Provision invited account'}
            </button>
          </form>
          {lastToken ? (
            <p className="token-callout" data-testid="activation-token">
              Activation token (out-of-band only): <code>{lastToken}</code>
            </p>
          ) : null}

          <h3>Accounts</h3>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Login</th>
                  <th>Name</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {accounts.map((account) => (
                  <tr key={account.id}>
                    <td>{account.login_identifier}</td>
                    <td>{account.display_name}</td>
                    <td>{account.status}</td>
                    <td>
                      {account.status === 'active' && account.id !== identity.user_id ? (
                        <button
                          type="button"
                          className="secondary-button"
                          disabled={busy}
                          onClick={() => handleDisable(account)}
                        >
                          Disable
                        </button>
                      ) : null}
                      {account.status === 'disabled' ? (
                        <button
                          type="button"
                          className="secondary-button"
                          disabled={busy}
                          onClick={() => handleEnable(account)}
                        >
                          Reactivate
                        </button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </article>
      ) : null}

      {canManageMemberships ? (
        <article className="admin-card">
          <h3>Facility memberships</h3>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>User</th>
                  <th>Facility</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {memberships.map((membership) => (
                  <tr key={membership.id}>
                    <td>{accountLabel(membership.user_id)}</td>
                    <td>{membership.facility_id.slice(0, 8)}</td>
                    <td>{membership.status}</td>
                    <td>
                      {membership.status === 'active' ? (
                        <button
                          type="button"
                          className="secondary-button"
                          disabled={busy}
                          onClick={() => handleEndMembership(membership)}
                        >
                          End
                        </button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </article>
      ) : null}

      {canDelegate ? (
        <article className="admin-card">
          <h3>Role bindings</h3>
          <form onSubmit={handleCreateBinding} className="admin-form">
            <div className="field">
              <label htmlFor="binding-user">Target user id</label>
              <input
                id="binding-user"
                value={bindingUserId}
                onChange={(event) => setBindingUserId(event.target.value)}
                required
              />
            </div>
            <div className="field">
              <label htmlFor="binding-role">Role</label>
              <select
                id="binding-role"
                value={bindingRoleCode}
                onChange={(event) => setBindingRoleCode(event.target.value)}
              >
                <option value="VHT">VHT</option>
                <option value="HCW">HCW</option>
                <option value="FA">FA</option>
                <option value="AUTHOR">AUTHOR</option>
                <option value="ANALYST">ANALYST</option>
                <option value="PA">PA</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="binding-facility">Facility</label>
              <select
                id="binding-facility"
                value={bindingFacilityId}
                onChange={(event) => setBindingFacilityId(event.target.value)}
              >
                {facilityOptions.map((facility) => (
                  <option key={facility.id} value={facility.id}>
                    {facility.code}
                  </option>
                ))}
              </select>
            </div>
            <button type="submit" className="primary-button" disabled={busy}>
              {busy ? 'Working…' : 'Create binding'}
            </button>
          </form>

          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>User</th>
                  <th>Role</th>
                  <th>Scope</th>
                  <th>Ends</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {bindings.map((binding) => (
                  <tr key={binding.id}>
                    <td>{accountLabel(binding.user_id)}</td>
                    <td>{binding.role_code}</td>
                    <td>
                      {binding.scope_kind}
                      {binding.facility_id ? ` · ${binding.facility_id.slice(0, 8)}` : ''}
                    </td>
                    <td>{binding.ends_at ? 'revoked' : 'active'}</td>
                    <td>
                      {binding.ends_at === null ? (
                        <button
                          type="button"
                          className="secondary-button"
                          disabled={busy}
                          onClick={() => handleRevokeBinding(binding)}
                        >
                          Revoke
                        </button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </article>
      ) : null}
    </section>
  )
}
