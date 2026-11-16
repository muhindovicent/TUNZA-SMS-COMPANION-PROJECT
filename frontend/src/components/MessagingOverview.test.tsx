import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { StaffIdentity } from '../api/auth'
import { ApiError, apiFetch, apiFetchPage } from '../api/client'
import { loadFoundationStatus } from '../api/health'
import MessagingOverview from './MessagingOverview'
import StaffShell from './StaffShell'

vi.mock('../api/client', async (original) => ({ ...await original<typeof import('../api/client')>(), apiFetch: vi.fn(), apiFetchPage: vi.fn() }))
vi.mock('../api/health', () => ({ loadFoundationStatus: vi.fn() }))
const care: StaffIdentity = { user_id: 'care', display_name: 'Synthetic Care', state: 'active', bindings: [],
  facilities: [{ id: 'facility-a', code: 'SYN-A', name: 'Synthetic facility' }], permissions: ['message.read', 'message.plan'] }
const ops: StaffIdentity = { ...care, user_id: 'ops', permissions: ['message.ops', 'messaging.control'] }
const row = { id: 'occurrence-a', facility_id: 'facility-a', kind: 'weekly', state: 'held', version: 1,
  hold_reasons: ['D05_unconfigured', 'D07_unconfigured'], due_at: '2027-01-01T06:00:00Z', expires_at: '2027-01-02T06:00:00Z',
  patient_name: 'SYNTHETIC PRIVATE NAME', text: 'SYNTHETIC PRIVATE BODY', episode_id: 'private-episode' }

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(loadFoundationStatus).mockResolvedValue({ api: 'reachable', database: 'connected' })
  vi.mocked(apiFetchPage).mockResolvedValue({ data: [row], page: { next_cursor: null } })
  vi.mocked(apiFetch).mockImplementation(async (path, request) => {
    if (request?.method === 'POST') return {}
    if (path.includes('/budgets')) return []
    if (path.includes('/events')) return [{ sequence: 1, event_type: 'plan', state: 'held', hold_reasons: ['D07_unconfigured'], created_at: '2026-11-15T06:00:00Z' }]
    return { paused: true, version: 0, code: 'policy_missing' }
  })
})

async function loaded() {
  await screen.findByRole('button', { name: 'History occurrence-a' })
  await waitFor(() => expect(screen.queryByText('Loading scheduling…')).toBeNull())
}

describe('durable synthetic messaging overview', () => {
  it('offers permission-based navigation and redacted operations history', async () => {
    render(<StaffShell identity={ops} onSignedOut={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Messaging overview' }))
    await loaded()
    expect(apiFetchPage).toHaveBeenCalledWith(expect.stringContaining('view=operations'), expect.any(Object))
    expect(screen.queryByText(row.patient_name)).toBeNull()
    expect(screen.queryByText(row.text)).toBeNull()
    expect(screen.queryByText(row.episode_id)).toBeNull()
    expect(screen.queryByLabelText('Assigned episode ID')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'History occurrence-a' }))
    await screen.findByRole('article', { name: 'Scheduling history' })
    expect(apiFetch).toHaveBeenCalledWith('/message-occurrences/occurrence-a/events?view=operations')
  })

  it('hides messaging navigation from content-only roles', () => {
    render(<StaffShell identity={{ ...care, permissions: ['content.read'] }} onSignedOut={vi.fn()} />)
    expect(screen.queryByRole('button', { name: 'Messaging overview' })).toBeNull()
  })

  it('loads authorized facility options for platform operators without care memberships', async () => {
    const fetch = vi.mocked(apiFetch).getMockImplementation()!
    vi.mocked(apiFetch).mockImplementation(async (path, options) => path === '/facilities'
      ? care.facilities : fetch(path, options))
    render(<MessagingOverview identity={{ ...ops, facilities: [], permissions: [...ops.permissions, 'facility.read'] }} />)
    await screen.findByRole('option', { name: 'SYN-A' })
    fireEvent.change(screen.getByLabelText('Messaging facility'), { target: { value: 'facility-a' } })
    await screen.findByRole('button', { name: 'Reserve synthetic capacity' })
    expect(apiFetch).toHaveBeenCalledWith('/facilities', expect.objectContaining({ signal: expect.any(AbortSignal) }))
    expect(screen.queryByLabelText('Assigned episode ID')).toBeNull()
  })

  it('plans only explicit held fixture inputs with Kampala-to-UTC conversion', async () => {
    render(<MessagingOverview identity={care} />)
    await loaded()
    fireEvent.change(screen.getByLabelText('Assigned episode ID'), { target: { value: 'episode-fixture' } })
    fireEvent.change(screen.getByLabelText('Synthetic gestational index'), { target: { value: '4' } })
    fireEvent.change(screen.getByLabelText('Fixture due time (Africa/Kampala)'), { target: { value: '2027-01-01T09:00' } })
    fireEvent.change(screen.getByLabelText('Fixture expiry (Africa/Kampala)'), { target: { value: '2027-01-02T09:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Plan held candidate' }))
    await screen.findByText('Synthetic scheduling action saved. No SMS submission is authorized.')
    expect(apiFetch).toHaveBeenCalledWith('/message-occurrences?facility_id=facility-a', expect.objectContaining({ method: 'POST', body: {
      episode_id: 'episode-fixture', kind: 'weekly', gestational_week: 4,
      due_at: '2027-01-01T06:00:00.000Z', expires_at: '2027-01-02T06:00:00.000Z',
    } }))
  })

  it('rechecks using the occurrence version and keeps uncertain retry keys', async () => {
    const implementation = vi.mocked(apiFetch).getMockImplementation()!
    let failed = false
    vi.mocked(apiFetch).mockImplementation(async (path, request) => {
      if (request?.method === 'POST' && !failed) { failed = true; throw new ApiError('unavailable', 'Synthetic audit failure', 503) }
      return implementation(path, request)
    })
    render(<MessagingOverview identity={care} />)
    await loaded()
    fireEvent.click(screen.getByRole('button', { name: 'Recheck occurrence-a' }))
    await screen.findByText('Scheduling action unavailable. Retry the same details.')
    fireEvent.click(screen.getByRole('button', { name: 'Recheck occurrence-a' }))
    await screen.findByText('Synthetic scheduling action saved. No SMS submission is authorized.')
    const writes = vi.mocked(apiFetch).mock.calls.filter(([, options]) => options?.method === 'POST')
    expect(writes).toHaveLength(2)
    expect(writes[0][1]?.idempotencyKey).toBe(writes[1][1]?.idempotencyKey)
    expect(writes[1][1]?.ifMatch).toBe('"1"')
  })

  it('recovers a confirmed write with GETs when refreshing fails', async () => {
    render(<MessagingOverview identity={care} />)
    await loaded()
    vi.mocked(apiFetchPage).mockRejectedValue(new ApiError('unavailable', 'Synthetic read failure', 503))
    fireEvent.click(screen.getByRole('button', { name: 'Recheck occurrence-a' }))
    const saved = 'Action saved. Refresh scheduling to recover the view before another write.'
    await screen.findByText(saved)
    expect(screen.queryByText('Scheduling action unavailable. Retry the same details.')).toBeNull()
    expect(screen.getByRole('button', { name: 'Plan held candidate' })).toBeDisabled()
    vi.mocked(apiFetchPage).mockResolvedValue({ data: [row], page: { next_cursor: null } })
    fireEvent.click(screen.getByRole('button', { name: 'Refresh scheduling' }))
    await waitFor(() => expect(screen.queryByText(saved)).toBeNull())
    expect(vi.mocked(apiFetch).mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1)
  })

  it('uses explicit global pause versions and empty synthetic policy configuration', async () => {
    render(<MessagingOverview identity={ops} />)
    await loaded()
    fireEvent.change(screen.getByLabelText('Synthetic control code'), { target: { value: 'SYN-PAUSE' } })
    fireEvent.click(screen.getByRole('button', { name: 'Unpause fixture scope' }))
    await screen.findByText('Synthetic scheduling action saved. No SMS submission is authorized.')
    expect(apiFetch).toHaveBeenCalledWith('/messaging/global/pause', expect.objectContaining({ ifMatch: '"0"', body: {
      paused: false, code: 'SYN-PAUSE', config: {},
    } }))
    expect(screen.getByLabelText('Explicit synthetic cap')).toHaveValue('')
    expect(screen.getByLabelText('Explicit SYN cost per segment')).toHaveValue('')
  })

  it('does not retain operational records after authorization failure', async () => {
    vi.mocked(apiFetchPage).mockRejectedValue(new ApiError('forbidden', 'Unavailable', 403))
    render(<MessagingOverview identity={ops} />)
    await screen.findByText('Messaging overview unavailable.')
    expect(screen.queryByRole('button', { name: 'History occurrence-a' })).toBeNull()
  })

  it('creates an explicitly priced synthetic budget using UTC periods', async () => {
    render(<MessagingOverview identity={ops} />)
    await loaded()
    fireEvent.change(screen.getByLabelText('Budget start (Africa/Kampala)'), { target: { value: '2027-01-01T09:00' } })
    fireEvent.change(screen.getByLabelText('Budget end (Africa/Kampala)'), { target: { value: '2027-02-01T09:00' } })
    fireEvent.change(screen.getByLabelText('Explicit synthetic cap'), { target: { value: '10.5' } })
    fireEvent.change(screen.getByLabelText('Explicit SYN cost per segment'), { target: { value: '0.25' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create synthetic budget' }))
    await screen.findByText('Synthetic scheduling action saved. No SMS submission is authorized.')
    expect(apiFetch).toHaveBeenCalledWith('/messaging/global/budgets', expect.objectContaining({ body: {
      period_start: '2027-01-01T06:00:00.000Z', period_end: '2027-02-01T06:00:00.000Z',
      currency: 'SYN', synthetic_only: true, cap: '10.5', unit_cost: '0.25',
    } }))
  })

  it('shows explicit capacity holds without offering message submission', async () => {
    render(<MessagingOverview identity={ops} />)
    await loaded()
    fireEvent.change(screen.getByLabelText('Messaging facility'), { target: { value: 'facility-a' } })
    await screen.findByRole('button', { name: 'Reserve synthetic capacity' })
    await waitFor(() => expect(screen.queryByText('Loading scheduling…')).toBeNull())
    const fetch = vi.mocked(apiFetch).getMockImplementation()!
    vi.mocked(apiFetch).mockImplementation(async (path, options) => options?.method === 'POST'
      ? { reserved: false, hold_reasons: ['budget_exhausted'], dispatch_eligible: false }
      : fetch(path, options))
    fireEvent.change(screen.getByLabelText('Encoding fixture'), { target: { value: 'unicode' } })
    fireEvent.change(screen.getByLabelText('Synthetic repeated units'), { target: { value: '36' } })
    fireEvent.click(screen.getByRole('button', { name: 'Reserve synthetic capacity' }))
    await screen.findByText('Synthetic capacity held: budget_exhausted.')
    expect(apiFetch).toHaveBeenCalledWith('/messaging/facilities/facility-a/capacity-reservations', expect.objectContaining({ body: {
      fixture_encoding: 'unicode', units: 36,
    } }))
    expect(screen.queryByRole('button', { name: /send|submit SMS/i })).toBeNull()
  })

  it('passes pagination cursors and state filters without clinical fields', async () => {
    vi.mocked(apiFetchPage).mockResolvedValueOnce({ data: [row], page: { next_cursor: 'signed-cursor' } })
      .mockResolvedValue({ data: [{ ...row, id: 'occurrence-b' }], page: { next_cursor: null } })
    render(<MessagingOverview identity={ops} />)
    await loaded()
    fireEvent.click(screen.getByRole('button', { name: 'Load more occurrences' }))
    await screen.findByRole('button', { name: 'History occurrence-b' })
    expect(apiFetchPage).toHaveBeenCalledWith(expect.stringContaining('cursor=signed-cursor'), expect.any(Object))
    fireEvent.change(screen.getByLabelText('Occurrence status'), { target: { value: 'expired' } })
    await waitFor(() => expect(apiFetchPage).toHaveBeenCalledWith(expect.stringContaining('state=expired'), expect.any(Object)))
  })
})
