import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import ActivationPage from './ActivationPage'

interface ActivationCall {
  url: string
  method: string
  headers: Record<string, string>
  body: unknown
}

function stubActivation(result: { status: number; body?: unknown } | 'network-error'): ActivationCall[] {
  const calls: ActivationCall[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = String(input)
      const method = init.method ?? 'GET'
      const headers = (init.headers ?? {}) as Record<string, string>
      calls.push({ url, method, headers, body: init.body })
      if (url.endsWith('/auth/csrf')) {
        return Response.json({ data: { csrf_token: 'csrf-activation-token' } })
      }
      if (url.endsWith('/auth/activations')) {
        if (result === 'network-error') throw new Error('synthetic-network-failure')
        return Response.json(result.body ?? {}, { status: result.status })
      }
      return Response.json(
        { error: { code: 'not_found', message: 'not found', request_id: 'r' } },
        { status: 404 },
      )
    }),
  )
  return calls
}

async function fillActivation(token: string, password: string, confirm: string) {
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('Activation token'), token)
  await user.type(screen.getByLabelText('New password'), password)
  await user.type(screen.getByLabelText('Confirm password'), confirm)
  return user
}

describe('ActivationPage', () => {
  it('keeps activation disabled until token and matching strong passwords are provided', () => {
    stubActivation({ status: 200 })
    render(<ActivationPage onActivated={() => {}} />)
    const submit = screen.getByRole('button', { name: 'Activate account' })
    expect(submit).toBeDisabled()
    expect(screen.getByRole('heading', { name: 'Activate your staff account' })).toBeInTheDocument()
  })

  it('blocks submit when passwords are short or do not match', async () => {
    stubActivation({ status: 200 })
    render(<ActivationPage onActivated={() => {}} />)
    const user = await fillActivation('token-value', 'short', 'short')
    expect(screen.getByRole('button', { name: 'Activate account' })).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('at least 12 characters')

    await user.clear(screen.getByLabelText('New password'))
    await user.clear(screen.getByLabelText('Confirm password'))
    await user.type(screen.getByLabelText('New password'), 'synthetic-password')
    await user.type(screen.getByLabelText('Confirm password'), 'different-password')
    expect(screen.getByRole('button', { name: 'Activate account' })).toBeDisabled()
    expect(screen.getAllByRole('alert').some((node) => node.textContent?.includes('match'))).toBe(
      true,
    )
  })

  it('posts the activation with a CSRF token and reports success', async () => {
    const calls = stubActivation({
      status: 200,
      body: { data: { state: 'activated', mfa_required: false } },
    })
    const onActivated = vi.fn()
    render(<ActivationPage onActivated={onActivated} />)
    const user = await fillActivation(
      'one-time-activation-token',
      'synthetic-password',
      'synthetic-password',
    )
    await user.click(screen.getByRole('button', { name: 'Activate account' }))

    expect(onActivated).toHaveBeenCalledTimes(1)
    const csrfIndex = calls.findIndex((call) => call.url.endsWith('/auth/csrf'))
    const activationIndex = calls.findIndex((call) => call.url.endsWith('/auth/activations'))
    expect(csrfIndex).toBeGreaterThanOrEqual(0)
    expect(activationIndex).toBeGreaterThan(csrfIndex)
    expect(calls[activationIndex].method).toBe('POST')
    expect(calls[activationIndex].headers['X-CSRF-Token']).toBe('csrf-activation-token')
    expect(JSON.parse(String(calls[activationIndex].body))).toEqual({
      token: 'one-time-activation-token',
      new_password: 'synthetic-password',
    })
  })

  it('shows the server message when the activation token is rejected', async () => {
    stubActivation({
      status: 400,
      body: {
        error: {
          code: 'invalid_token',
          message: 'That activation link is no longer valid.',
          request_id: 'request-9',
        },
      },
    })
    const onActivated = vi.fn()
    render(<ActivationPage onActivated={onActivated} />)
    const user = await fillActivation(
      'stale-token',
      'synthetic-password',
      'synthetic-password',
    )
    await user.click(screen.getByRole('button', { name: 'Activate account' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'That activation link is no longer valid.',
    )
    expect(onActivated).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Activate account' })).toBeEnabled()
  })

  it('shows a connection message when the activation request fails on the network', async () => {
    stubActivation('network-error')
    const onActivated = vi.fn()
    render(<ActivationPage onActivated={onActivated} />)
    const user = await fillActivation(
      'network-token',
      'synthetic-password',
      'synthetic-password',
    )
    await user.click(screen.getByRole('button', { name: 'Activate account' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Unable to reach the server. Check your connection and try again.',
    )
    expect(onActivated).not.toHaveBeenCalled()
  })
})
