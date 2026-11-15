import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch, apiFetchPage } from '../api/client'
import type { StaffIdentity } from '../api/auth'
import AppointmentManagement from './AppointmentManagement'
import type { Appointment } from './AppointmentManagement'
import PatientRegistry from './PatientRegistry'

vi.mock('../api/client', async (original) => ({
  ...await original<typeof import('../api/client')>(), apiFetch: vi.fn(), apiFetchPage: vi.fn(),
}))

const identity: StaffIdentity = { user_id: 'synthetic-hcw', display_name: 'Synthetic HCW', state: 'active',
  facilities: [{ id: 'clinic-a', code: 'SYN-A', name: 'Synthetic Clinic A' }], bindings: [],
  permissions: ['patient.read', 'pregnancy.read', 'appointment.read', 'appointment.schedule',
    'appointment.confirm', 'appointment.reschedule', 'appointment.cancel', 'appointment.attend'] }
const episode = { id: 'pregnancy-a', version: 1, assignment_id: 'assignment-a', lifecycle: 'ongoing' }
const initial: Appointment = { id: 'booking-a', starts_at: '2027-06-15T06:00:00Z', status: 'scheduled',
  version: 1, episode_version: 2, supersedes_id: null, superseded_by_id: null,
  reminder_eligible: false, reminder_hold_reason: 'scheduled' }
let rows: Appointment[]
const changed = vi.fn<() => Promise<void>>()

beforeEach(() => {
  vi.resetAllMocks()
  changed.mockResolvedValue()
  rows = [{ ...initial }]
  vi.mocked(apiFetchPage).mockImplementation(async () => ({ data: structuredClone(rows), page: { next_cursor: null } }))
  vi.mocked(apiFetch).mockImplementation(async (path, request) => {
    if (path.includes('/events')) return [{ id: 'event-a', event_type: 'scheduled', sequence: 1,
      recorded_by: 'synthetic-hcw', effective_at: '2026-11-15T06:00:00Z',
      data: { reason: 'Synthetic booking history', related_appointment_id: null } }]
    if (path === '/patients/search') return [{ id: 'patient-a', facility_id: 'clinic-a', local_reference: 'SYN-ADULT-TEST', language: 'en', version: 1 }]
    if (path.startsWith('/pregnancy-episodes?')) return [{ ...episode, patient_id: 'patient-a', communication_state: 'paused', dating_summary: null }]
    if (path.includes('/dating-revisions')) return []
    if (path.startsWith('/pregnancy-episodes/')) return { ...episode, patient_id: 'patient-a', communication_state: 'paused', dating_summary: null }
    if (request?.method === 'POST' && path.includes('/reschedule')) {
      rows = [{ ...initial, status: 'rescheduled', version: 2, superseded_by_id: 'booking-b', reminder_hold_reason: 'superseded' },
        { ...initial, id: 'booking-b', starts_at: '2027-06-20T08:00:00Z', supersedes_id: 'booking-a' }]
      return rows[1]
    }
    if (request?.method === 'POST' && path.includes('/confirm')) {
      rows = [{ ...initial, status: 'confirmed', version: 2, reminder_hold_reason: 'D06_unconfigured' }]
      return rows[0]
    }
    return { ...initial }
  })
})

function mount(permissions = identity.permissions) {
  return render(<AppointmentManagement identity={{ ...identity, permissions }} episode={episode}
    facilityId="clinic-a" onChanged={changed} />)
}

async function loaded() {
  await screen.findByRole('article', { name: 'Booking booking-a' })
  await waitFor(() => expect(screen.queryByText('Loading appointments…')).toBeNull())
}

describe('selected pregnancy appointment management', () => {
  it('loads scoped bookings, displays Kampala time, and gates unconfirmed attendance', async () => {
    mount()
    await loaded()
    expect(screen.getByRole('heading', { name: /09:00:00.*Africa\/Kampala/ })).toBeInTheDocument()
    expect(screen.getByText('Reminder held: scheduled')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Record attendance' })).toBeNull()
    expect(apiFetchPage).toHaveBeenCalledWith('/appointments?facility_id=clinic-a&episode_id=pregnancy-a', expect.anything())
  })

  it('sends UTC staff-entered time, idempotency and pregnancy If-Match on scheduling', async () => {
    mount(); await loaded()
    fireEvent.change(screen.getByLabelText('Appointment time (Africa/Kampala)'), { target: { value: '2027-06-15T09:30' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save scheduled appointment' }))
    await screen.findByText('Appointment saved. History is preserved; reminders remain held.')
    expect(apiFetch).toHaveBeenCalledWith('/appointments?facility_id=clinic-a', expect.objectContaining({
      method: 'POST', idempotencyKey: expect.any(String), ifMatch: '"1"',
      body: { episode_facility_assignment_id: 'assignment-a', starts_at: '2027-06-15T06:30:00.000Z' },
    }))
    expect(changed).toHaveBeenCalled()
  })

  it('keeps the same idempotency key when retrying an uncertain mutation', async () => {
    const implementation = vi.mocked(apiFetch).getMockImplementation()!
    let failed = false
    vi.mocked(apiFetch).mockImplementation(async (path, request) => {
      if (path.includes('/confirm') && !failed) { failed = true; throw new ApiError('unavailable', 'Synthetic failure', 503) }
      return implementation(path, request)
    })
    mount(); await loaded()
    fireEvent.click(screen.getByRole('button', { name: 'Confirm appointment' }))
    await screen.findByText('Appointment could not be saved. Retry with the same details.')
    fireEvent.click(screen.getByRole('button', { name: 'Confirm appointment' }))
    await screen.findByText('Appointment saved. History is preserved; reminders remain held.')
    const writes = vi.mocked(apiFetch).mock.calls.filter(([path]) => path.includes('/confirm'))
    expect(writes[0][1]?.idempotencyKey).toBe(writes[1][1]?.idempotencyKey)
    expect(screen.getByText('Reminder held: D06_unconfigured')).toBeInTheDocument()
  })

  it('preserves superseded booking display and requires separate replacement confirmation', async () => {
    mount(); await loaded()
    fireEvent.click(screen.getByRole('button', { name: 'Reschedule appointment' }))
    fireEvent.change(screen.getByLabelText('Appointment time (Africa/Kampala)'), { target: { value: '2027-06-20T11:00' } })
    fireEvent.change(screen.getByLabelText('Appointment reason'), { target: { value: 'Synthetic replacement request' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save appointment action' }))
    await screen.findByText('Replacement booking: booking-b')
    const original = screen.getByRole('article', { name: 'Booking booking-a' })
    expect(within(original).queryByRole('button', { name: 'Confirm appointment' })).toBeNull()
    const replacement = screen.getByRole('article', { name: 'Booking booking-b' })
    expect(within(replacement).getByRole('button', { name: 'Confirm appointment' })).toBeInTheDocument()
    expect(within(replacement).queryByRole('button', { name: 'Record attendance' })).toBeNull()
    fireEvent.click(within(original).getByRole('button', { name: 'View appointment history' }))
    await screen.findByText('Synthetic booking history')
  })

  it('records an explicit staff attendance outcome with a UTC effective time', async () => {
    rows = [{ ...initial, status: 'confirmed', version: 2, reminder_hold_reason: 'D06_unconfigured' }]
    mount(); await loaded()
    fireEvent.click(screen.getByRole('button', { name: 'Record attendance' }))
    expect(screen.getByLabelText('Staff-recorded attendance')).toHaveValue('')
    fireEvent.change(screen.getByLabelText('Staff-recorded attendance'), { target: { value: 'missed' } })
    fireEvent.change(screen.getByLabelText('Recorded effective time (Africa/Kampala)'), { target: { value: '2027-06-15T10:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save appointment action' }))
    await screen.findByText('Appointment saved. History is preserved; reminders remain held.')
    expect(apiFetch).toHaveBeenCalledWith('/appointments/booking-a/attendance?facility_id=clinic-a', expect.objectContaining({
      ifMatch: '"2"', body: { status: 'missed', effective_at: '2027-06-15T07:00:00.000Z' },
    }))
  })

  it('hides mutations for read-only staff while retaining authorized history access', async () => {
    mount(['appointment.read']); await loaded()
    expect(screen.queryByRole('button', { name: 'Confirm appointment' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Save scheduled appointment' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Reschedule appointment' })).toBeNull()
    expect(screen.getByRole('button', { name: 'View appointment history' })).toBeInTheDocument()
  })

  it('reports stale versions without pretending the action succeeded', async () => {
    vi.mocked(apiFetch).mockRejectedValue(new ApiError('precondition_failed', 'Synthetic stale write', 412))
    mount(); await loaded()
    fireEvent.click(screen.getByRole('button', { name: 'Confirm appointment' }))
    await screen.findByText('The booking changed or conflicts. Refresh appointments before retrying.')
    expect(changed).not.toHaveBeenCalled()
    expect(screen.getByText('Status: scheduled · Version: 1')).toBeInTheDocument()
  })

  it('hides records and mutation controls after assignment revocation', async () => {
    vi.mocked(apiFetch).mockRejectedValue(new ApiError('not_found', 'Unavailable', 404))
    mount(); await loaded()
    fireEvent.click(screen.getByRole('button', { name: 'Confirm appointment' }))
    await screen.findByText('Appointment access is unavailable.')
    expect(screen.queryByRole('article', { name: 'Booking booking-a' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Save scheduled appointment' })).toBeNull()
  })

  it('loads additional cursor pages without changing patient or facility scope', async () => {
    vi.mocked(apiFetchPage).mockResolvedValueOnce({ data: [{ ...initial }], page: { next_cursor: 'signed-cursor' } })
      .mockResolvedValueOnce({ data: [{ ...initial, id: 'booking-b' }], page: { next_cursor: null } })
    mount(); await loaded()
    fireEvent.click(screen.getByRole('button', { name: 'Load more appointments' }))
    await screen.findByRole('article', { name: 'Booking booking-b' })
    expect(apiFetchPage).toHaveBeenLastCalledWith('/appointments?facility_id=clinic-a&episode_id=pregnancy-a&cursor=signed-cursor', expect.anything())
    expect(screen.getByRole('article', { name: 'Booking booking-a' })).toBeInTheDocument()
  })

  it('is reached through the selected registry patient and pregnancy', async () => {
    render(<PatientRegistry identity={identity} />)
    fireEvent.change(screen.getByLabelText('Synthetic reference'), { target: { value: 'SYN-ADULT-TEST' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search assigned patients' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Manage pregnancy episodes' }))
    await screen.findByRole('heading', { name: 'ANC appointments' })
    await loaded()
    expect(apiFetchPage).toHaveBeenCalledWith('/appointments?facility_id=clinic-a&episode_id=pregnancy-a', expect.anything())
  })

  it('submits a reasoned cancellation using the booking version and preserves the terminal booking', async () => {
    const implementation = vi.mocked(apiFetch).getMockImplementation()!
    vi.mocked(apiFetch).mockImplementation(async (path, request) => {
      if (path.includes('/cancel')) {
        rows = [{ ...initial, status: 'cancelled', version: 2, reminder_hold_reason: 'cancelled' }]
        return rows[0]
      }
      return implementation(path, request)
    })
    mount(); await loaded()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel appointment' }))
    fireEvent.change(screen.getByLabelText('Appointment reason'), { target: { value: 'Synthetic cancellation' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save appointment action' }))
    await screen.findByText('Status: cancelled · Version: 2')
    expect(apiFetch).toHaveBeenCalledWith('/appointments/booking-a/cancel?facility_id=clinic-a', expect.objectContaining({
      ifMatch: '"1"', body: { reason: 'Synthetic cancellation' },
    }))
    expect(screen.queryByRole('button', { name: 'Confirm appointment' })).toBeNull()
  })

  it('does not offer scheduling or disclose bookings when initial authorization fails', async () => {
    vi.mocked(apiFetchPage).mockRejectedValue(new ApiError('not_found', 'Unavailable', 404))
    mount()
    await screen.findByText('Appointments are unavailable.')
    expect(screen.queryByRole('button', { name: 'Save scheduled appointment' })).toBeNull()
    expect(screen.queryByRole('article', { name: 'Booking booking-a' })).toBeNull()
  })
})
