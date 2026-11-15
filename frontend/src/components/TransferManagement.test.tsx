import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch, apiFetchPage } from '../api/client'
import type { StaffIdentity } from '../api/auth'
import TransferManagement from './TransferManagement'
import PatientRegistry from './PatientRegistry'

vi.mock('../api/client', async (original) => ({
  ...await original<typeof import('../api/client')>(), apiFetch: vi.fn(), apiFetchPage: vi.fn(),
}))

const source: StaffIdentity = { user_id: 'source-hcw', display_name: 'Synthetic HCW', state: 'active',
  facilities: [{ id: 'facility-a', code: 'SYN-A', name: 'Synthetic A' }], bindings: [],
  permissions: ['patient.read', 'pregnancy.read', 'transfer.initiate', 'transfer.cancel'] }
const administrator: StaffIdentity = { ...source, user_id: 'destination-fa', permissions: ['transfer.accept', 'transfer.cancel'],
  facilities: [{ id: 'facility-b', code: 'SYN-B', name: 'Synthetic B' }] }
const episode = { id: 'episode-a', version: 1, assignment_id: 'assignment-a', lifecycle: 'ongoing' }
const pending = { id: 'request-a', source_facility_id: 'facility-a', destination_facility_id: 'facility-b',
  state: 'pending', version: 1, synthetic: true }
const changed = vi.fn<() => Promise<void>>()
let enabled: boolean
let rows: (typeof pending & { decision?: { outcome: string; reason_code: string; destination_reference?: string } })[]

beforeEach(() => {
  vi.resetAllMocks()
  changed.mockResolvedValue()
  enabled = true
  rows = [{ ...pending }]
  vi.mocked(apiFetchPage).mockImplementation(async (path) => {
    const state = new URL(path, 'https://synthetic.test').searchParams.get('state')
    return { data: structuredClone(rows.filter((row) => state === 'all' || row.state === state)), page: { next_cursor: null } }
  })
  vi.mocked(apiFetch).mockImplementation(async (path, request) => {
    if (path === '/transfers/capabilities') return { synthetic_enabled: enabled, live_enabled: false, unresolved_decisions: ['D04', 'D09'] }
    if (path.startsWith('/transfers/destinations')) return [{ id: 'facility-b', code: 'SYN-B', name: 'Synthetic B' }]
    if (path.includes('/receivers')) return [{ id: 'membership-b', display_name: 'Existing synthetic HCW B' }]
    if (path.includes('/handover')) return { synthetic: true, fixture_code: 'SYN-HANDOVER-TEST' }
    if (request?.method === 'POST' && path.includes('/decision')) {
      const input = request.body as { decision: string; reason: string }
      rows = [{ ...pending, state: input.decision === 'accept' ? 'accepted' : 'rejected', version: 2,
        decision: { outcome: input.decision, reason_code: input.reason,
          ...(input.decision === 'accept' ? { destination_reference: 'SYN-ADULT-DESTINATION-FIXTURE' } : {}) } }]
      return structuredClone(rows[0])
    }
    if (request?.method === 'POST' && path.includes('/cancel')) {
      rows = [{ ...pending, state: 'cancelled', version: 2 }]
      return rows[0]
    }
    if (request?.method === 'POST' && path.startsWith('/transfers?')) {
      rows = [{ ...pending }]
      return rows[0]
    }
    if (path === '/patients/search') return [{ id: 'patient-a', facility_id: 'facility-a', local_reference: 'SYN-ADULT-TEST', language: 'en', version: 1 }]
    if (path.startsWith('/pregnancy-episodes?')) return [{ ...episode, patient_id: 'patient-a', communication_state: 'paused', dating_summary: null }]
    if (path.includes('/dating-revisions')) return []
    return { ...episode, patient_id: 'patient-a', communication_state: 'paused', dating_summary: null }
  })
})

function outgoing() {
  return render(<TransferManagement identity={source} facilityId="facility-a" episode={episode} onChanged={changed} />)
}

function incoming() { return render(<TransferManagement identity={administrator} />) }

async function settled() {
  await waitFor(() => expect(screen.queryByText('Loading transfer records…')).toBeNull())
}

describe('controlled synthetic transfers', () => {
  it('leaves fixture actions disabled while D04/D09 remain unconfigured', async () => {
    enabled = false
    outgoing()
    await screen.findByText('Synthetic transfers are disabled. D04/D09 approvals remain pending.')
    expect(apiFetchPage).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: 'Request synthetic transfer' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Accept synthetic transfer' })).toBeNull()
  })

  it('initiates only bounded fixture metadata from the selected pregnancy', async () => {
    rows = []
    outgoing()
    await settled()
    fireEvent.change(screen.getByLabelText('Synthetic destination'), { target: { value: 'facility-b' } })
    fireEvent.change(screen.getByLabelText('Synthetic handover fixture code'), { target: { value: 'SYN-HANDOVER-TEST' } })
    fireEvent.click(screen.getByRole('button', { name: 'Request synthetic transfer' }))
    await screen.findByText(/Synthetic transfer saved/)
    expect(apiFetch).toHaveBeenCalledWith('/transfers?facility_id=facility-a', expect.objectContaining({
      method: 'POST', idempotencyKey: expect.any(String), ifMatch: '"1"', body: {
        source_assignment_id: 'assignment-a', destination_facility_id: 'facility-b',
        handover: { synthetic: true, fixture_code: 'SYN-HANDOVER-TEST' },
      },
    }))
    expect(screen.queryByLabelText(/Clinical notes/)).toBeNull()
    expect(changed).toHaveBeenCalled()
  })

  it('requires explicit destination HCW and fixture language for acceptance', async () => {
    incoming(); await settled()
    fireEvent.click(screen.getByRole('button', { name: 'Accept synthetic transfer' }))
    await screen.findByRole('option', { name: 'Existing synthetic HCW B' })
    expect(screen.getByLabelText('Existing destination HCW')).toHaveValue('')
    expect(screen.getByLabelText('Synthetic destination language')).toHaveValue('')
    fireEvent.change(screen.getByLabelText('Existing destination HCW'), { target: { value: 'membership-b' } })
    fireEvent.change(screen.getByLabelText('Synthetic destination language'), { target: { value: 'lg' } })
    fireEvent.change(screen.getByLabelText('Synthetic decision reason code'), { target: { value: 'SYN-ACCEPT-TEST' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save synthetic transfer decision' }))
    await screen.findByText('Accepted synthetic destination reference: SYN-ADULT-DESTINATION-FIXTURE')
    expect(apiFetch).toHaveBeenCalledWith('/transfers/request-a/decision?facility_id=facility-b', expect.objectContaining({
      ifMatch: '"1"', body: { decision: 'accept', reason: 'SYN-ACCEPT-TEST', receiver_membership_id: 'membership-b', synthetic_language: 'lg' },
    }))
    expect(screen.queryByRole('button', { name: 'Accept synthetic transfer' })).toBeNull()
  })

  it('rejects without creating a destination assignment or selecting a receiver', async () => {
    incoming(); await settled()
    fireEvent.click(screen.getByRole('button', { name: 'Reject synthetic transfer' }))
    expect(screen.queryByLabelText('Existing destination HCW')).toBeNull()
    fireEvent.change(screen.getByLabelText('Synthetic decision reason code'), { target: { value: 'SYN-REJECT-TEST' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save synthetic transfer decision' }))
    await screen.findByText(/Synthetic transfer saved/)
    expect(apiFetch).toHaveBeenCalledWith('/transfers/request-a/decision?facility_id=facility-b', expect.objectContaining({
      body: { decision: 'reject', reason: 'SYN-REJECT-TEST' },
    }))
    expect(screen.queryByText(/Accepted synthetic destination reference/)).toBeNull()
  })

  it('supports source cancellation without destination decision controls', async () => {
    outgoing(); await settled()
    expect(screen.queryByRole('button', { name: 'Accept synthetic transfer' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel synthetic transfer' }))
    fireEvent.change(screen.getByLabelText('Synthetic decision reason code'), { target: { value: 'SYN-CANCEL-TEST' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save synthetic transfer decision' }))
    await screen.findByText('State: cancelled · Version: 2')
    expect(apiFetch).toHaveBeenCalledWith('/transfers/request-a/cancel?facility_id=facility-a', expect.objectContaining({
      body: { reason: 'SYN-CANCEL-TEST' }, ifMatch: '"1"',
    }))
  })

  it('shows fixture handover only and does not fetch complete source records', async () => {
    incoming(); await settled()
    fireEvent.click(screen.getByRole('button', { name: 'View synthetic handover' }))
    await screen.findByText(/Fixture handover for request-a: SYN-HANDOVER-TEST/)
    expect(vi.mocked(apiFetch).mock.calls.every(([path]) => !path.startsWith('/patients') && !path.startsWith('/pregnancy-episodes'))).toBe(true)
  })

  it('reuses a command key after an uncertain destination decision', async () => {
    const implementation = vi.mocked(apiFetch).getMockImplementation()!
    let failed = false
    vi.mocked(apiFetch).mockImplementation(async (path, request) => {
      if (path.includes('/decision') && !failed) { failed = true; throw new ApiError('unavailable', 'Synthetic failure', 503) }
      return implementation(path, request)
    })
    incoming(); await settled()
    fireEvent.click(screen.getByRole('button', { name: 'Reject synthetic transfer' }))
    fireEvent.change(screen.getByLabelText('Synthetic decision reason code'), { target: { value: 'SYN-REJECT-TEST' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save synthetic transfer decision' }))
    await screen.findByText('Transfer could not be saved. Retry with the same details.')
    fireEvent.click(screen.getByRole('button', { name: 'Save synthetic transfer decision' }))
    await screen.findByText(/Synthetic transfer saved/)
    const calls = vi.mocked(apiFetch).mock.calls.filter(([path]) => path.includes('/decision'))
    expect(calls[0][1]?.idempotencyKey).toBe(calls[1][1]?.idempotencyKey)
  })

  it('reports stale decisions without claiming acceptance', async () => {
    const implementation = vi.mocked(apiFetch).getMockImplementation()!
    vi.mocked(apiFetch).mockImplementation(async (path, request) => {
      if (path.includes('/decision')) throw new ApiError('precondition_failed', 'Synthetic stale decision', 412)
      return implementation(path, request)
    })
    incoming(); await settled()
    fireEvent.click(screen.getByRole('button', { name: 'Reject synthetic transfer' }))
    fireEvent.change(screen.getByLabelText('Synthetic decision reason code'), { target: { value: 'SYN-REJECT-TEST' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save synthetic transfer decision' }))
    await screen.findByText('The transfer changed or conflicts. Refresh before retrying.')
    expect(screen.queryByText(/Synthetic transfer saved/)).toBeNull()
  })

  it('reaches source transfers through the selected patient registry workflow', async () => {
    render(<PatientRegistry identity={source} />)
    fireEvent.change(screen.getByLabelText('Synthetic reference'), { target: { value: 'SYN-ADULT-TEST' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search assigned patients' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Manage pregnancy episodes' }))
    await screen.findByRole('heading', { name: 'Controlled facility transfers' })
    await settled()
    expect(apiFetchPage).toHaveBeenCalledWith('/transfers?facility_id=facility-a&direction=outgoing&state=all&episode_id=episode-a', expect.anything())
  })

  it('supports a source administrator outgoing cancellation view without patient charts', async () => {
    incoming(); await settled()
    fireEvent.change(screen.getByLabelText('Transfer direction'), { target: { value: 'outgoing' } })
    await screen.findByRole('button', { name: 'Cancel synthetic transfer' })
    expect(screen.queryByRole('button', { name: 'Accept synthetic transfer' })).toBeNull()
    expect(apiFetchPage).toHaveBeenLastCalledWith('/transfers?facility_id=facility-b&direction=outgoing&state=pending', expect.anything())
  })
})
