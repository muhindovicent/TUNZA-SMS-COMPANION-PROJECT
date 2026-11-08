import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import LoginPage from './LoginPage'

interface LoginCall {
  url: string
  method: string
  headers: Record<string, string>
}

function stubLogin(login: { status: number; body?: unknown } | 'network-error'): LoginCall[] {
  const calls: LoginCall[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = String(input)
      const method = init.method ?? 'GET'
      const headers = (init.headers ?? {}) as Record<string, string>
      calls.push({ url, method, headers })
      if (url.endsWith('/auth/csrf')) {
        return Response.json({ data: { csrf_token: 'csrf-page-token' } })
      }
      if (url.endsWith('/auth/login')) {
        if (login === 'network-error') throw new Error('synthetic-network-failure')
        return Response.json(login.body ?? {}, { status: login.status })
      }
      return Response.json(
        { error: { code: 'not_found', message: 'not found', request_id: 'r' } },
        { status: 404 },
      )
    }),
  )
  return calls
}

async function typeCredentials(identifier: string, password: string) {
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('Login identifier'), identifier)
  await user.type(screen.getByLabelText('Password'), password)
  return user
}

describe('LoginPage', () => {
  it('renders a provided session notice', () => {
    stubLogin({ status: 401 })
    render(<LoginPage onSignedIn={() => {}} notice="Unable to reach the server to verify your session." />)
    expect(screen.getByRole('status')).toHaveTextContent(
      'Unable to reach the server to verify your session.',
    )
    expect(screen.getByRole('heading', { name: 'Staff sign in' })).toBeInTheDocument()
  })

  it('keeps sign-in disabled until both fields are filled', async () => {
    stubLogin({ status: 401 })
    render(<LoginPage onSignedIn={() => {}} />)
    const submit = screen.getByRole('button', { name: 'Sign in' })
    expect(submit).toBeDisabled()

    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Login identifier'), 'synthetic.vht')
    expect(submit).toBeDisabled()

    await user.type(screen.getByLabelText('Password'), 'synthetic-password')
    expect(submit).toBeEnabled()
  })

  it('requests a CSRF token before posting credentials', async () => {
    const calls = stubLogin({ status: 200, body: { data: { state: 'active', user_id: 'u' } } })
    const onSignedIn = vi.fn()
    render(<LoginPage onSignedIn={onSignedIn} />)
    const user = await typeCredentials('synthetic.vht', 'synthetic-password')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(onSignedIn).toHaveBeenCalledTimes(1)
    const csrfIndex = calls.findIndex((call) => call.url.endsWith('/auth/csrf'))
    const loginIndex = calls.findIndex((call) => call.url.endsWith('/auth/login'))
    expect(csrfIndex).toBeGreaterThanOrEqual(0)
    expect(loginIndex).toBeGreaterThan(csrfIndex)
    expect(calls[loginIndex].headers['X-CSRF-Token']).toBe('csrf-page-token')
    expect(screen.getByLabelText('Password')).toHaveValue('')
  })

  it('shows the generic server message when credentials are rejected', async () => {
    stubLogin({
      status: 401,
      body: {
        error: {
          code: 'invalid_credentials',
          message: 'Sign-in details could not be verified.',
          request_id: 'request-3',
        },
      },
    })
    const onSignedIn = vi.fn()
    render(<LoginPage onSignedIn={onSignedIn} />)
    const user = await typeCredentials('synthetic.vht', 'wrong-password')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Sign-in details could not be verified.',
    )
    expect(onSignedIn).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeEnabled()
  })

  it('shows the rate-limit message when the server throttles sign-in', async () => {
    stubLogin({
      status: 429,
      body: {
        error: {
          code: 'rate_limited',
          message: 'Too many sign-in attempts. Try again shortly.',
          request_id: 'request-4',
        },
      },
    })
    render(<LoginPage onSignedIn={() => {}} />)
    const user = await typeCredentials('synthetic.vht', 'synthetic-password')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Too many sign-in attempts. Try again shortly.',
    )
  })

  it('shows a connection message when the sign-in request fails on the network', async () => {
    stubLogin('network-error')
    render(<LoginPage onSignedIn={() => {}} />)
    const user = await typeCredentials('synthetic.vht', 'synthetic-password')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Unable to reach the server. Check your connection and try again.',
    )
  })
})
