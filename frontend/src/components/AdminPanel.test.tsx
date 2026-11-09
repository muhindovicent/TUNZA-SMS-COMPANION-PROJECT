import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import AdminPanel from './AdminPanel'
import type { StaffIdentity } from '../api/auth'

const adminIdentity: StaffIdentity = {
  user_id: 'user-admin',
  display_name: 'Test PA',
  state: 'active',
  facilities: [{ id: 'facility-a', code: 'SYNTH-A', name: 'Synthetic Clinic A' }],
  bindings: [{ role_code: 'PA', role_name: 'PA', scope_kind: 'platform', facility_id: null }],
  permissions: [
    'account.provision',
    'membership.manage',
    'role.delegate',
    'facility.read',
  ],
}

const vhtIdentity: StaffIdentity = {
  ...adminIdentity,
  user_id: 'user-vht',
  bindings: [{ role_code: 'VHT', role_name: 'VHT', scope_kind: 'facility', facility_id: 'facility-a' }],
  permissions: ['facility.read'],
}

function stubFetch(routes: Record<string, unknown>) {
  const calls: { url: string; method: string }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = String(input)
      const method = init.method ?? 'GET'
      calls.push({ url, method })
      if (url.endsWith('/auth/csrf')) {
        return Response.json({ data: { csrf_token: 'csrf-test-token' } })
      }
      for (const [suffix, payload] of Object.entries(routes)) {
        if (url.endsWith(suffix) && method in (payload as object) === false) {
          continue
        }
      }
      if (url.endsWith('/users') && method === 'GET') {
        return Response.json({
          data: [
            {
              id: 'acct-1',
              login_identifier: 'synthetic.fa',
              display_name: 'Synthetic FA',
              status: 'active',
              version: 1,
            },
          ],
        })
      }
      if (url.endsWith('/memberships') && method === 'GET') {
        return Response.json({
          data: [
            {
              id: 'mem-1',
              user_id: 'acct-1',
              facility_id: 'facility-a',
              status: 'active',
              starts_at: '2026-01-01T00:00:00+00:00',
              ends_at: null,
              version: 1,
            },
          ],
        })
      }
      if (url.endsWith('/memberships') && method === 'POST') {
        return Response.json(
          {
            data: {
              id: 'mem-new',
              user_id: 'acct-1',
              facility_id: 'facility-a',
              status: 'active',
              starts_at: '2026-01-01T00:00:00+00:00',
              ends_at: null,
              version: 1,
            },
          },
          { status: 201 },
        )
      }
      if (url.endsWith('/role-bindings') && method === 'GET') {
        return Response.json({
          data: [
            {
              id: 'bind-1',
              user_id: 'acct-1',
              role_code: 'VHT',
              role_name: 'VHT',
              scope_kind: 'facility',
              facility_id: 'facility-a',
              starts_at: '2026-01-01T00:00:00+00:00',
              ends_at: null,
              granted_by: 'user-admin',
              version: 1,
            },
          ],
        })
      }
      if (url.endsWith('/users') && method === 'POST') {
        return Response.json(
          {
            data: {
              account: {
                id: 'acct-new',
                login_identifier: 'synthetic.new',
                display_name: 'Synthetic New',
                status: 'invited',
                version: 1,
              },
              facility_id: 'facility-a',
              activation_token: 'one-time-token',
            },
          },
          { status: 201 },
        )
      }
      return Response.json(
        { error: { code: 'not_found', message: 'not found', request_id: 'r' } },
        { status: 404 },
      )
    }),
  )
  return calls
}

describe('AdminPanel', () => {
  it('lists accounts, memberships and bindings for an administrator', async () => {
    stubFetch({})
    render(<AdminPanel identity={adminIdentity} />)

    expect((await screen.findAllByText('synthetic.fa')).length).toBeGreaterThan(0)
    expect(await screen.findByRole('button', { name: 'Disable' })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'End' })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Revoke' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Provision account' })).toBeInTheDocument()
  })

  it('provisions an account and surfaces the one-time activation token', async () => {
    stubFetch({})
    render(<AdminPanel identity={adminIdentity} />)
    await screen.findByRole('button', { name: 'Disable' })

    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Login identifier'), 'synthetic.new')
    await user.type(screen.getByLabelText('Display name'), 'Synthetic New')
    await user.click(screen.getByRole('button', { name: 'Provision invited account' }))

    expect(await screen.findByTestId('activation-token')).toHaveTextContent('one-time-token')
    expect(screen.getByRole('status')).toHaveTextContent('Account provisioned')
  })

  it('assigns a facility membership through the membership form', async () => {
    stubFetch({})
    render(<AdminPanel identity={adminIdentity} />)
    expect(await screen.findByRole('button', { name: 'Assign membership' })).toBeDisabled()

    const user = userEvent.setup()
    await user.selectOptions(screen.getByLabelText('Staff account'), 'acct-1')
    await user.selectOptions(screen.getByLabelText('Assign to facility'), 'facility-a')
    await user.click(screen.getByRole('button', { name: 'Assign membership' }))

    expect(await screen.findByRole('status')).toHaveTextContent('Facility membership assigned.')
    expect(screen.getByRole('button', { name: 'Assign membership' })).toBeDisabled()
  })

  it('does not render administration sections for a VHT identity', async () => {
    stubFetch({})
    render(<AdminPanel identity={vhtIdentity} />)

    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: 'Provision account' })).not.toBeInTheDocument()
      expect(screen.queryByRole('heading', { name: 'Facility memberships' })).not.toBeInTheDocument()
      expect(screen.queryByRole('heading', { name: 'Role bindings' })).not.toBeInTheDocument()
    })
  })
})
