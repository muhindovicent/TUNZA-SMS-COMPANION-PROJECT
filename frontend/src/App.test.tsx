import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import App from './App'

function mockHealthyServices() {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => Response.json({
    data: url.endsWith('/live')
      ? { service: 'tunza-api', status: 'ok' }
      : { service: 'tunza-api', status: 'ready', checks: { database: 'connected' } },
  })))
}

describe('TUNZA foundation shell', () => {
  it('renders branding, accessible navigation and checking state', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})))
    render(<App />)
    expect(screen.getByRole('link', { name: 'TUNZA home' })).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Main navigation' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Connected care.')
    expect(screen.getByRole('button', { name: 'Checking connection…' })).toBeDisabled()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /sign in|register|send sms/i })).not.toBeInTheDocument()
    expect(screen.getByText('Synthetic development data only')).toBeInTheDocument()
  })

  it('shows actual healthy API and database results', async () => {
    mockHealthyServices()
    render(<App />)
    expect(await within(screen.getByRole('article', { name: 'Flask API' })).findByText('Reachable')).toBeInTheDocument()
    expect(within(screen.getByRole('article', { name: 'PostgreSQL' })).getByText('Connected')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Check connection' })).toBeEnabled()
  })

  it('shows an outage and lets the user retry successfully', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('synthetic-error')))
    const user = userEvent.setup()
    render(<App />)
    await waitFor(() => expect(screen.getAllByText('Unavailable')).toHaveLength(2))
    expect(screen.queryByText('synthetic-error')).not.toBeInTheDocument()
    mockHealthyServices()
    await user.click(screen.getByRole('button', { name: 'Check connection' }))
    expect(await screen.findByText('Connected')).toBeInTheDocument()
    expect(screen.getByText(/Frontend, API and database are connected/)).toBeInTheDocument()
  })

  it('cancels in-flight probes when the shell unmounts', () => {
    const signals: AbortSignal[] = []
    vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => {
      signals.push(options.signal as AbortSignal)
      return new Promise(() => {})
    }))
    const { unmount } = render(<App />)
    unmount()
    expect(signals).toHaveLength(2)
    expect(signals.every((signal) => signal.aborted)).toBe(true)
  })
})
