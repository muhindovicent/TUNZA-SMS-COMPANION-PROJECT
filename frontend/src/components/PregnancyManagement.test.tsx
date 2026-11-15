import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { StaffIdentity } from '../api/auth'
import { ApiError, apiFetch } from '../api/client'
import PregnancyManagement from './PregnancyManagement'
import PatientRegistry from './PatientRegistry'

vi.mock('../api/client', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/client')>(), apiFetch: vi.fn(),
}))

const patient = { id: 'synthetic-patient', facility_id: 'clinic-a',
  local_reference: 'SYN-ADULT-TEST', language: 'en', version: 1 }
const identity: StaffIdentity = { user_id: 'hcw', display_name: 'Synthetic HCW', state: 'active',
  facilities: [{ id: 'clinic-a', code: 'SYN-A', name: 'Synthetic clinic A' },
               { id: 'clinic-b', code: 'SYN-B', name: 'Synthetic clinic B' }], bindings: [],
  permissions: ['patient.read', 'patient.register', 'pregnancy.read', 'pregnancy.create', 'pregnancy.date'] }
const originalEpisode = { id: 'synthetic-episode', patient_id: patient.id, facility_id: patient.facility_id,
  lifecycle: 'ongoing', communication_state: 'paused', version: 1, dating_summary: null }
const originalRevision = { id: 'revision-1', episode_id: 'synthetic-episode', basis: 'synthetic-method',
  estimated_due_date: '2027-06-15', reason: 'Synthetic estimate', recorded_by: 'hcw',
  created_at: '2026-11-15T10:00:00Z', revision_sequence: 1, supersedes_id: null as string | null, parent_version: 2 }

let episode: typeof originalEpisode
let history: typeof originalRevision[]

beforeEach(() => {
  vi.resetAllMocks()
  episode = { ...originalEpisode }
  history = []
  vi.mocked(apiFetch).mockImplementation(async (path, request) => {
    if (path === '/patients') return { patient }
    if (path === '/patients/search') return [patient]
    if (request?.method === 'POST' && path.includes('/dating-revisions')) {
      const body = request.body as { basis: string; estimated_due_date: string; reason: string }
      const revision = { ...originalRevision, ...body, id: `revision-${history.length + 1}`,
        revision_sequence: history.length + 1, parent_version: episode.version + 1,
        supersedes_id: history[0]?.id ?? null }
      history = [revision, ...history]
      episode = { ...episode, version: episode.version + 1 }
      return revision
    }
    if (path.includes('/dating-revisions')) return structuredClone(history)
    if (path.startsWith('/pregnancy-episodes?')) return [{ ...episode }]
    return { ...episode }
  })
})

async function fillDatingForm() {
  await screen.findByRole('button', { name: 'Record dating revision' })
  await waitFor(() => expect(screen.queryByText('Loading pregnancy records…')).toBeNull())
  fireEvent.change(screen.getByLabelText('Recorded method'), { target: { value: 'synthetic-method' } })
  fireEvent.change(screen.getByLabelText('Estimated due date'), { target: { value: '2027-06-15' } })
  fireEvent.change(screen.getByLabelText('Reason for revision'), { target: { value: 'Synthetic estimate' } })
}

describe('registry-linked pregnancy management', () => {
  it('uses the authorized patient and facility selected through the registry', async () => {
    render(<PatientRegistry identity={identity} />)
    expect(screen.queryByRole('button', { name: 'Manage pregnancy episodes' })).toBeNull()
    fireEvent.change(screen.getByLabelText('Synthetic reference'), { target: { value: patient.local_reference } })
    fireEvent.click(screen.getByRole('button', { name: 'Search assigned patients' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Manage pregnancy episodes' }))
    await screen.findByText('No dating revisions recorded.')
    expect(apiFetch).toHaveBeenCalledWith(
      `/pregnancy-episodes?patient_id=${patient.id}&facility_id=${patient.facility_id}`, expect.anything(),
    )
    fireEvent.change(screen.getByLabelText('Facility'), { target: { value: 'clinic-b' } })
    expect(screen.queryByRole('heading', { name: /Pregnancy episodes/ })).toBeNull()
  })

  it('retrieves the existing registration episode and never offers a duplicate ongoing episode', async () => {
    render(<PregnancyManagement identity={identity} patient={patient} />)
    await screen.findByText('No dating revisions recorded.')
    expect(screen.queryByRole('button', { name: 'Create pregnancy episode' })).toBeNull()
    expect(screen.getByText('paused')).toBeInTheDocument()
    expect(vi.mocked(apiFetch).mock.calls.every(([, request]) => request?.method !== 'POST')).toBe(true)
  })

  it('records a date-only EDD with idempotency and the parent version, and reloads history', async () => {
    render(<PregnancyManagement identity={identity} patient={patient} />)
    await fillDatingForm()
    expect(screen.getByLabelText('Estimated due date')).toHaveAttribute('type', 'date')
    fireEvent.click(screen.getByRole('button', { name: 'Record dating revision' }))
    await screen.findByText('Dating revision recorded. Previous revisions are preserved.')
    const write = vi.mocked(apiFetch).mock.calls.find(([, request]) => request?.method === 'POST')
    expect(write?.[1]).toMatchObject({ ifMatch: '"1"', idempotencyKey: expect.any(String),
      body: { estimated_due_date: '2027-06-15', basis: 'synthetic-method', reason: 'Synthetic estimate' } })
    expect(screen.getByText('Revision 1: 2027-06-15')).toBeInTheDocument()
  })

  it('shows persisted correction history after remount and hides dating writes from readers', async () => {
    history = [{ ...originalRevision, id: 'revision-2', revision_sequence: 2, supersedes_id: 'revision-1',
                 estimated_due_date: '2027-06-20' }, originalRevision]
    render(<PregnancyManagement identity={{ ...identity, permissions: ['pregnancy.read'] }} patient={patient} />)
    await screen.findByText('Revision 2: 2027-06-20')
    expect(screen.getByText('Revision 1: 2027-06-15')).toBeInTheDocument()
    expect(screen.getByText('Supersedes revision-1')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Record dating revision' })).toBeNull()
  })

  it('retains the idempotency key when retrying an uncertain dating write', async () => {
    const implementation = vi.mocked(apiFetch).getMockImplementation()!
    let failed = false
    vi.mocked(apiFetch).mockImplementation(async (path, request) => {
      if (request?.method === 'POST' && !failed) { failed = true; throw new ApiError('unavailable', 'Synthetic failure', 503) }
      return implementation(path, request)
    })
    render(<PregnancyManagement identity={identity} patient={patient} />)
    await fillDatingForm()
    fireEvent.click(screen.getByRole('button', { name: 'Record dating revision' }))
    await screen.findByText('Dating revision could not be recorded. Retry with the same details.')
    fireEvent.click(screen.getByRole('button', { name: 'Record dating revision' }))
    await screen.findByText('Dating revision recorded. Previous revisions are preserved.')
    const writes = vi.mocked(apiFetch).mock.calls.filter(([, request]) => request?.method === 'POST')
    expect(writes[0][1]?.idempotencyKey).toBe(writes[1][1]?.idempotencyKey)
  })

  it('reports authorization failure and suppresses creation when initial loading fails', async () => {
    vi.mocked(apiFetch).mockRejectedValue(new ApiError('not_found', 'Unavailable', 404))
    render(<PregnancyManagement identity={identity} patient={patient} />)
    await screen.findByText('Pregnancy records are unavailable.')
    expect(screen.queryByRole('button', { name: 'Create pregnancy episode' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Record dating revision' })).toBeNull()
  })

  it('creates a subsequent episode only when no ongoing episode exists', async () => {
    episode = { ...originalEpisode, lifecycle: 'completed' }
    const implementation = vi.mocked(apiFetch).getMockImplementation()!
    vi.mocked(apiFetch).mockImplementation(async (path, request) => {
      if (path === '/pregnancy-episodes' && request?.method === 'POST') {
        episode = { ...originalEpisode, id: 'new-episode' }
        return episode
      }
      return implementation(path, request)
    })
    render(<PregnancyManagement identity={identity} patient={patient} />)
    const create = await screen.findByRole('button', { name: 'Create pregnancy episode' })
    fireEvent.click(create)
    await screen.findByText('Synthetic pregnancy episode created.')
    expect(apiFetch).toHaveBeenCalledWith('/pregnancy-episodes', expect.objectContaining({
      method: 'POST', body: { patient_id: patient.id, facility_id: patient.facility_id },
      idempotencyKey: expect.any(String),
    }))
  })
})
