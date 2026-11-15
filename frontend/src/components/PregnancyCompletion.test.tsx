import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { StaffIdentity } from '../api/auth'
import { ApiError, apiFetch, apiFetchPage } from '../api/client'
import PregnancyCompletion from './PregnancyCompletion'
import PregnancyManagement from './PregnancyManagement'

vi.mock('../api/client', async (original) => ({
  ...await original<typeof import('../api/client')>(), apiFetch: vi.fn(), apiFetchPage: vi.fn(),
}))

const identity: StaffIdentity = { user_id: 'hcw', display_name: 'Synthetic HCW', state: 'active',
  facilities: [{ id: 'facility-a', code: 'SYN-A', name: 'Synthetic A' }], bindings: [],
  permissions: ['patient.read', 'pregnancy.read', 'pregnancy.complete', 'pregnancy.date',
    'appointment.read', 'appointment.schedule', 'appointment.confirm', 'appointment.cancel'] }
const initialEpisode = { id: 'episode-a', version: 1, lifecycle: 'ongoing', communication_state: 'paused',
  assignment_id: 'assignment-a', completion_record_id: null as string | null, patient_id: 'patient-a',
  facility_id: 'facility-a', dating_summary: null }
const initialRecord = { id: 'record-1', completed_on: '2026-11-15', reason: 'Synthetic closure', sequence: 1,
  parent_version: 2, supersedes_id: null as string | null, recorded_by: 'hcw',
  recorded_at: '2026-11-15T06:00:00Z', closed_at: '2026-11-15T06:00:00Z',
  cancelled_appointment_count: 1, lifecycle: 'completed', communication_state: 'completed' }
let episode: typeof initialEpisode
let records: typeof initialRecord[]
const changed = vi.fn<() => Promise<void>>()

beforeEach(() => {
  vi.resetAllMocks()
  changed.mockResolvedValue()
  episode = { ...initialEpisode }; records = []
  vi.mocked(apiFetchPage).mockImplementation(async () => ({ data: [{ id: 'booking-a',
    starts_at: '2027-06-15T06:00:00Z', status: episode.lifecycle === 'completed' ? 'cancelled' : 'scheduled',
    version: 1, episode_version: episode.version, reminder_eligible: false, reminder_hold_reason: 'episode_completed' }],
    page: { next_cursor: null } }))
  vi.mocked(apiFetch).mockImplementation(async (path, request) => {
    if (path.includes('/completion')) {
      if (request?.method === 'POST') {
        const body = request.body as { completed_on: string; reason: string; supersedes_id?: string }
        const row = { ...initialRecord, id: `record-${records.length + 1}`, completed_on: body.completed_on,
          reason: body.reason, sequence: records.length + 1, supersedes_id: body.supersedes_id ?? null,
          parent_version: episode.version + 1, cancelled_appointment_count: body.supersedes_id ? 0 : 1 }
        records = [row, ...records]
        episode = { ...episode, lifecycle: 'completed', communication_state: 'completed',
          completion_record_id: row.id, version: row.parent_version }
        return row
      }
      return structuredClone(records)
    }
    if (path.includes('/dating-revisions')) return []
    if (path.startsWith('/pregnancy-episodes?')) return [{ ...episode }]
    return { ...episode }
  })
})

function mount(propsEpisode = episode, propsIdentity = identity) {
  return render(<PregnancyCompletion identity={propsIdentity} facilityId="facility-a" episode={propsEpisode} onChanged={changed} />)
}

async function fill() {
  await screen.findByText('No completion records.')
  fireEvent.change(screen.getByLabelText('Staff-recorded completion date'), { target: { value: '2026-11-15' } })
  fireEvent.change(screen.getByLabelText('Completion reason'), { target: { value: 'Synthetic closure' } })
}

describe('pregnancy lifecycle completion', () => {
  it('requires explicit confirmation before posting an empty-outcome completion', async () => {
    mount(); await fill()
    fireEvent.click(screen.getByRole('button', { name: 'Review completion' }))
    expect(screen.getByRole('group', { name: 'Confirm pregnancy completion' })).toBeInTheDocument()
    expect(vi.mocked(apiFetch).mock.calls.every(([, request]) => request?.method !== 'POST')).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Confirm pregnancy completion' }))
    await screen.findByText('Completion saved. History is preserved; pregnancy messaging remains ineligible.')
    expect(apiFetch).toHaveBeenCalledWith('/pregnancy-episodes/episode-a/completion?facility_id=facility-a', expect.objectContaining({
      ifMatch: '"1"', idempotencyKey: expect.any(String), method: 'POST',
      body: { completed_on: '2026-11-15', approved_outcome_fields: {}, reason: 'Synthetic closure' },
    }))
    expect(screen.getByText('Future current bookings cancelled: 1')).toBeInTheDocument()
  })

  it('allows backing out without changing the pregnancy', async () => {
    mount(); await fill()
    fireEvent.click(screen.getByRole('button', { name: 'Review completion' }))
    fireEvent.click(screen.getByRole('button', { name: 'Back to completion details' }))
    expect(screen.getByLabelText('Completion reason')).toHaveValue('Synthetic closure')
    expect(vi.mocked(apiFetch).mock.calls.every(([, request]) => request?.method !== 'POST')).toBe(true)
  })

  it('does not fetch or expose completion history without permission', () => {
    mount(episode, { ...identity, permissions: ['pregnancy.read'] })
    expect(screen.queryByRole('heading', { name: 'Pregnancy completion' })).toBeNull()
    expect(apiFetch).not.toHaveBeenCalled()
  })

  it('appends corrections against the current pointer and preserves earlier records', async () => {
    records = [{ ...initialRecord }]
    episode = { ...episode, lifecycle: 'completed', communication_state: 'completed', completion_record_id: 'record-1', version: 2 }
    mount()
    await screen.findByRole('button', { name: 'Record completion correction' })
    fireEvent.click(screen.getByRole('button', { name: 'Record completion correction' }))
    fireEvent.change(screen.getByLabelText('Staff-recorded completion date'), { target: { value: '2026-11-14' } })
    fireEvent.change(screen.getByLabelText('Completion reason'), { target: { value: 'Synthetic correction' } })
    fireEvent.click(screen.getByRole('button', { name: 'Review completion' }))
    expect(screen.getByText(/original closure instant and appointment history remain unchanged/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Confirm pregnancy completion' }))
    await screen.findByText('Completion revision 2 — 2026-11-14')
    expect(screen.getByText('Completion revision 1 — 2026-11-15')).toBeInTheDocument()
    const write = vi.mocked(apiFetch).mock.calls.find(([, request]) => request?.method === 'POST')
    expect(write?.[1]).toMatchObject({ ifMatch: '"2"', body: { supersedes_id: 'record-1', approved_outcome_fields: {} } })
  })

  it('preserves a frozen preview version and reports stale writes', async () => {
    const view = mount(); await fill()
    fireEvent.click(screen.getByRole('button', { name: 'Review completion' }))
    episode = { ...episode, version: 2 }
    view.rerender(<PregnancyCompletion identity={identity} facilityId="facility-a" episode={episode} onChanged={changed} />)
    const implementation = vi.mocked(apiFetch).getMockImplementation()!
    vi.mocked(apiFetch).mockImplementation(async (path, request) => {
      if (request?.method === 'POST') throw new ApiError('precondition_failed', 'Synthetic stale version', 412)
      return implementation(path, request)
    })
    fireEvent.click(screen.getByRole('button', { name: 'Confirm pregnancy completion' }))
    await screen.findByText('The pregnancy changed or conflicts. Refresh before confirming again.')
    expect(vi.mocked(apiFetch).mock.calls.find(([, request]) => request?.method === 'POST')?.[1]?.ifMatch).toBe('"1"')
  })

  it('retains its idempotency key for uncertain confirmed requests', async () => {
    const implementation = vi.mocked(apiFetch).getMockImplementation()!
    let failed = false
    vi.mocked(apiFetch).mockImplementation(async (path, request) => {
      if (request?.method === 'POST' && !failed) { failed = true; throw new ApiError('unavailable', 'Synthetic audit failure', 503) }
      return implementation(path, request)
    })
    mount(); await fill()
    fireEvent.click(screen.getByRole('button', { name: 'Review completion' }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm pregnancy completion' }))
    await screen.findByText('Completion could not be saved. Retry the same confirmed details.')
    fireEvent.click(screen.getByRole('button', { name: 'Confirm pregnancy completion' }))
    await screen.findByText('Completion saved. History is preserved; pregnancy messaging remains ineligible.')
    const writes = vi.mocked(apiFetch).mock.calls.filter(([, request]) => request?.method === 'POST')
    expect(writes[0][1]?.idempotencyKey).toBe(writes[1][1]?.idempotencyKey)
  })

  it('keeps legacy completed rows visible without inventing completion history', async () => {
    mount({ ...episode, lifecycle: 'completed' })
    await screen.findByText('This legacy completed episode has no recorded completion history.')
    expect(screen.queryByRole('button', { name: 'Review completion' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Record completion correction' })).toBeNull()
  })

  it('integrates closure into the selected pregnancy and refreshes invalidated appointments', async () => {
    render(<PregnancyManagement identity={identity} patient={{ id: 'patient-a', facility_id: 'facility-a', local_reference: 'SYN-ADULT-TEST' }} />)
    await fill()
    await waitFor(() => expect(screen.queryByText('Loading appointments…')).toBeNull())
    fireEvent.click(screen.getByRole('button', { name: 'Review completion' }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm pregnancy completion' }))
    await screen.findByText('Completion saved. History is preserved; pregnancy messaging remains ineligible.')
    await screen.findByText('Status: cancelled · Version: 1')
    expect(screen.queryByRole('button', { name: 'Record dating revision' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Save scheduled appointment' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Confirm appointment' })).toBeNull()
    expect(screen.getByRole('option', { name: 'completed — episode-a' })).toBeInTheDocument()
  })

  it('warns explicitly that an existing withdrawal is preserved', async () => {
    mount({ ...episode, communication_state: 'withdrawn' }); await fill()
    fireEvent.click(screen.getByRole('button', { name: 'Review completion' }))
    expect(screen.getByText('The existing withdrawn state will be preserved.')).toBeInTheDocument()
  })
})
