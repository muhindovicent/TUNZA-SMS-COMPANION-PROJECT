import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import App from './App'
import type { StaffIdentity } from './api/auth'

const staffIdentity: StaffIdentity = {
  user_id: 'user-1',
  display_name: 'Test VHT A',
  state: 'active',
  facilities: [{ id: 'facility-a', code: 'SYNTH-A', name: 'Synthetic Clinic A' }],
  bindings: [
    { role_code: 'VHT', role_name: 'VHT', scope_kind: 'facility', facility_id: 'facility-a' },
  ],
  permissions: ['facility.read'],
}

interface RouteCall {
  url: string
  method: string
  headers: Record<string, string>
  body: unknown
  signal: unknown
}

interface ApiState {
  me: 'pending' | 'unauthorized' | 'network-error' | StaffIdentity
  health: 'healthy' | 'outage' | 'pending'
  login: { status: number; body?: unknown }
}

function stubApi(options: Partial<ApiState> = {}): { calls: RouteCall[]; state: ApiState } {
  const state: ApiState = {
    me: options.me ?? 'unauthorized',
    health: options.health ?? 'healthy',
    login: options.login ?? { status: 200, body: { data: { state: 'active', user_id: 'user-1' } } },
  }
  const calls: RouteCall[] = []

  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = String(input)
      const method = init.method ?? 'GET'
      const headers = (init.headers ?? {}) as Record<string, string>
      calls.push({ url, method, headers, body: init.body, signal: init.signal })

      if (url.endsWith('/auth/me')) {
        if (state.me === 'pending') return new Promise<Response>(() => {})
        if (state.me === 'network-error') throw new Error('synthetic-network-failure')
        if (state.me === 'unauthorized') {
          return Response.json(
            {
              error: {
                code: 'unauthorized',
                message: 'The request could not be completed.',
                request_id: 'request-1',
              },
            },
            { status: 401 },
          )
        }
        return Response.json({ data: state.me })
      }
      if (url.endsWith('/auth/csrf')) {
        return Response.json({ data: { csrf_token: 'csrf-test-token' } })
      }
      if (url.endsWith('/auth/login')) {
        if (state.login.status >= 200 && state.login.status < 300) {
          state.me = staffIdentity
        }
        return Response.json(state.login.body ?? {}, { status: state.login.status })
      }
      if (url.endsWith('/auth/activations')) {
        return Response.json(
          { data: { state: 'activated', mfa_required: false } },
          { status: 200 },
        )
      }
      if (url.endsWith('/auth/logout')) {
        state.me = 'unauthorized'
        return new Response(null, { status: 204 })
      }
      if (url.endsWith('/health/live')) {
        if (state.health === 'pending') return new Promise<Response>(() => {})
        return Response.json(
          state.health === 'healthy'
            ? { data: { service: 'tunza-api', status: 'ok' } }
            : { error: { code: 'service_unavailable', message: 'unavailable', request_id: 'r' } },
          { status: state.health === 'healthy' ? 200 : 503 },
        )
      }
      if (url.endsWith('/health/ready')) {
        if (state.health === 'pending') return new Promise<Response>(() => {})
        return Response.json(
          state.health === 'healthy'
            ? { data: { service: 'tunza-api', status: 'ready', checks: { database: 'connected' } } }
            : { error: { code: 'service_unavailable', message: 'unavailable', request_id: 'r' } },
          { status: state.health === 'healthy' ? 200 : 503 },
        )
      }
      return Response.json(
        { error: { code: 'not_found', message: 'not found', request_id: 'r' } },
        { status: 404 },
      )
    }),
  )

  return { calls, state }
}

async function submitSignIn(identifier: string, password: string) {
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('Login identifier'), identifier)
  await user.type(screen.getByLabelText('Password'), password)
  await user.click(screen.getByRole('button', { name: 'Sign in' }))
}

describe('TUNZA staff session shell', () => {
  it('shows a session check while the initial identity request is pending', () => {
    stubApi({ me: 'pending' })
    render(<App />)
    expect(screen.getByRole('status')).toHaveTextContent('Checking your session…')
    expect(screen.queryByRole('heading', { name: 'Staff sign in' })).not.toBeInTheDocument()
    expect(screen.queryByText('Connected care.')).not.toBeInTheDocument()
  })

  it('renders the sign-in page for an unauthenticated visitor', async () => {
    stubApi({ me: 'unauthorized' })
    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Staff sign in' })).toBeInTheDocument()
    expect(screen.getByLabelText('Login identifier')).toBeInTheDocument()
    expect(screen.getByLabelText('Password')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeDisabled()
    expect(screen.queryByText('Connected care.')).not.toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Main navigation' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /register|sign up/i })).not.toBeInTheDocument()
  })

  it('renders the secure activation page when the hash route is #activate', async () => {
    stubApi({ me: 'unauthorized' })
    window.location.hash = '#activate'
    render(<App />)

    expect(
      await screen.findByRole('heading', { name: 'Activate your staff account' }),
    ).toBeInTheDocument()
    expect(screen.getByLabelText('Activation token')).toBeInTheDocument()
    expect(screen.getByLabelText('New password')).toBeInTheDocument()
    expect(screen.getByLabelText('Confirm password')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Staff sign in' })).not.toBeInTheDocument()

    window.location.hash = ''
  })

  it('returns to sign-in after activation and shows the success notice', async () => {
    const { state } = stubApi({ me: 'unauthorized' })
    window.location.hash = '#activate'
    render(<App />)
    expect(
      await screen.findByRole('heading', { name: 'Activate your staff account' }),
    ).toBeInTheDocument()

    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Activation token'), 'one-time-token')
    await user.type(screen.getByLabelText('New password'), 'synthetic-password')
    await user.type(screen.getByLabelText('Confirm password'), 'synthetic-password')
    await user.click(screen.getByRole('button', { name: 'Activate account' }))

    expect(await screen.findByRole('heading', { name: 'Staff sign in' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Account activated. Sign in with your new password.')
    expect(state.me).toBe('unauthorized')

    window.location.hash = ''
  })

  it('shows a session notice when the identity request cannot reach the server', async () => {
    stubApi({ me: 'network-error' })
    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Staff sign in' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent(
      'Unable to reach the server to verify your session.',
    )
  })

  it('renders the signed-in shell with identity, role and facility badges', async () => {
    stubApi({ me: staffIdentity })
    render(<App />)

    expect(await screen.findByText('Connected care.')).toBeInTheDocument()
    expect(screen.getByText('Test VHT A')).toBeInTheDocument()
    expect(screen.getByText('VHT')).toBeInTheDocument()
    expect(screen.getByText('SYNTH-A')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Main navigation' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /sign in/i })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Password')).not.toBeInTheDocument()

    expect(await within(screen.getByRole('article', { name: 'Flask API' })).findByText('Reachable')).toBeInTheDocument()
    expect(within(screen.getByRole('article', { name: 'PostgreSQL' })).getByText('Connected')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Check connection' })).toBeEnabled()
  })

  it('signs in with a CSRF-bound login request and enters the shell', async () => {
    const { calls } = stubApi({ me: 'unauthorized' })
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Staff sign in' })).toBeInTheDocument()

    await submitSignIn('synthetic.vht', 'synthetic-password')

    expect(await screen.findByText('Connected care.')).toBeInTheDocument()

    const csrfIndex = calls.findIndex((call) => call.url.endsWith('/auth/csrf'))
    const loginIndex = calls.findIndex((call) => call.url.endsWith('/auth/login'))
    expect(csrfIndex).toBeGreaterThanOrEqual(0)
    expect(loginIndex).toBeGreaterThan(csrfIndex)
    const loginCall = calls[loginIndex]
    expect(loginCall.method).toBe('POST')
    expect(loginCall.headers['X-CSRF-Token']).toBe('csrf-test-token')
    expect(JSON.parse(String(loginCall.body))).toEqual({
      login_identifier: 'synthetic.vht',
      password: 'synthetic-password',
    })
  })

  it('keeps the sign-in page and shows the generic failure for bad credentials', async () => {
    stubApi({
      me: 'unauthorized',
      login: {
        status: 401,
        body: {
          error: {
            code: 'invalid_credentials',
            message: 'Sign-in details could not be verified.',
            request_id: 'request-2',
          },
        },
      },
    })
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Staff sign in' })).toBeInTheDocument()

    await submitSignIn('synthetic.vht', 'wrong-password')

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Sign-in details could not be verified.',
    )
    expect(screen.getByRole('heading', { name: 'Staff sign in' })).toBeInTheDocument()
    expect(screen.queryByText('Connected care.')).not.toBeInTheDocument()
  })

  it('signs out through a CSRF-bound request and returns to the sign-in page', async () => {
    const { calls } = stubApi({ me: staffIdentity })
    render(<App />)
    expect(await screen.findByText('Connected care.')).toBeInTheDocument()

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Sign out' }))

    expect(await screen.findByRole('heading', { name: 'Staff sign in' })).toBeInTheDocument()
    const logoutCall = calls.find((call) => call.url.endsWith('/auth/logout'))
    expect(logoutCall).toBeDefined()
    expect(logoutCall?.method).toBe('POST')
    expect(logoutCall?.headers['X-CSRF-Token']).toBe('csrf-test-token')
  })

  it('shows an outage and lets the user retry successfully', async () => {
    const { state } = stubApi({ me: staffIdentity, health: 'outage' })
    const user = userEvent.setup()
    render(<App />)

    expect(await screen.findByText('Connected care.')).toBeInTheDocument()
    await waitFor(() => expect(screen.getAllByText('Unavailable')).toHaveLength(2))
    expect(screen.queryByText('synthetic-error')).not.toBeInTheDocument()

    state.health = 'healthy'
    await user.click(screen.getByRole('button', { name: 'Check connection' }))
    expect(await screen.findByText('Connected')).toBeInTheDocument()
    expect(screen.getByText(/Frontend, API and database are connected/)).toBeInTheDocument()
  })

  it('cancels in-flight probes when the shell unmounts', async () => {
    const { calls } = stubApi({ me: staffIdentity, health: 'pending' })
    const { unmount } = render(<App />)
    expect(await screen.findByText('Connected care.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Checking connection…' })).toBeDisabled()

    unmount()

    const signals = calls.map((call) => call.signal).filter(Boolean)
    expect(signals.length).toBeGreaterThan(0)
    expect(signals.every((signal) => (signal as AbortSignal).aborted)).toBe(true)
  })
})
