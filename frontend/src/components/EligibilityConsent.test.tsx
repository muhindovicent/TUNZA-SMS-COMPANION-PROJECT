import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import EligibilityConsent from './EligibilityConsent'
import type { StaffIdentity } from '../api/auth'
import { apiFetch } from '../api/client'

vi.mock('../api/client', () => ({ apiFetch: vi.fn() }))

const identity: StaffIdentity = {
  user_id: 'user', display_name: 'Synthetic staff', state: 'active', bindings: [],
  permissions: ['patient.read', 'patient.register', 'eligibility.record', 'consent.record', 'consent.read'],
  facilities: [{ id: 'facility', code: 'TEST', name: 'Synthetic clinic' }],
}

describe('synthetic eligibility and consent', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(apiFetch).mockResolvedValue([])
  })

  it('finds patient via search API with correct response structure', async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([{ id: 'patient', facility_id: 'facility', local_reference: 'SYN-ADULT-TEST' }])
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce({ data: [] })
    render(<EligibilityConsent identity={identity} />)
    fireEvent.change(screen.getByLabelText('Synthetic reference'), { target: { value: 'SYN-ADULT-TEST' } })
    fireEvent.click(screen.getByRole('button', { name: 'Find patient' }))
    await screen.findByText(/Patient:/)
    const searchCall = vi.mocked(apiFetch).mock.calls.find((call) => call[0] === '/patients/search')
    expect(searchCall).toBeDefined()
    expect(searchCall![1]?.body).toEqual({ facility_id: 'facility', local_reference: 'SYN-ADULT-TEST' })
  })

  it('records eligibility assessment', async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([{ id: 'patient', facility_id: 'facility', local_reference: 'SYN-ADULT-TEST' }])
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce({ data: [] })
      .mockResolvedValueOnce({} as Record<string, unknown>)
    render(<EligibilityConsent identity={identity} />)
    fireEvent.change(screen.getByLabelText('Synthetic reference'), { target: { value: 'SYN-ADULT-TEST' } })
    fireEvent.click(screen.getByRole('button', { name: 'Find patient' }))
    await screen.findByText(/Patient:/)
    fireEvent.change(screen.getByLabelText('Result'), { target: { value: 'verified' } })
    fireEvent.click(screen.getByRole('button', { name: 'Record eligibility check' }))
    await screen.findByText('Eligibility check recorded.')
    const eligibilityCall = vi.mocked(apiFetch).mock.calls.find((call) =>
      typeof call[0] === 'string' &&
      call[0].includes('/patients/patient/eligibility?facility_id=') &&
      call[1]?.method === 'POST'
    )
    expect(eligibilityCall).toBeDefined()
    expect(eligibilityCall![1]?.body).toEqual({ method: 'documentary', result: 'verified', checked_at: expect.any(String) })
  })

  it('records consent grant with notice and evidence', async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce([{ id: 'notice-1', notice_code: 'SYN-CONSENT-001', revision: 1, language: 'en', status: 'approved', purpose_codes: ['messaging'], effective_from: null, effective_to: null, version: 1 }])
      .mockResolvedValueOnce([{ id: 'patient', facility_id: 'facility', local_reference: 'SYN-ADULT-TEST' }])
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce({ data: [{ id: 'evidence-1', storage_reference: 'synthetic-evidence-ref', media_type: 'application/pdf', recorded_by: 'user', version: 1 }] })
      .mockResolvedValueOnce({} as Record<string, unknown>)
    render(<EligibilityConsent identity={identity} />)
    fireEvent.change(screen.getByLabelText('Synthetic reference'), { target: { value: 'SYN-ADULT-TEST' } })
    fireEvent.click(screen.getByRole('button', { name: 'Find patient' }))
    await screen.findByText(/Patient:/)
    fireEvent.change(screen.getByLabelText('Consent notice'), { target: { value: 'notice-1' } })
    fireEvent.change(screen.getByLabelText('Evidence reference'), { target: { value: 'evidence-1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Record consent grant' }))
    await screen.findByText('Consent grant recorded.')
    const consentCall = vi.mocked(apiFetch).mock.calls.find((call) =>
      typeof call[0] === 'string' &&
      call[0].includes('/patients/patient/consent-events?facility_id=') &&
      call[1]?.method === 'POST'
    )
    expect(consentCall).toBeDefined()
    expect(consentCall![1]?.body).toMatchObject({
      purpose: 'messaging',
      decision: 'grant',
      method: 'documentary',
      language: 'en',
      notice_version_id: 'notice-1',
      evidence_id: 'evidence-1',
    })
  })

  it('hides registration and consent without registration permission', () => {
    render(<EligibilityConsent identity={{ ...identity, permissions: ['patient.read', 'consent.read'] }} />)
    expect(screen.queryByRole('button', { name: 'Record eligibility check' })).toBeNull()
    expect(screen.queryByRole('button', { name: /Record consent/ })).toBeNull()
  })

  it('shows registration and consent with registration permission', async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([{ id: 'patient', facility_id: 'facility', local_reference: 'SYN-ADULT-TEST' }])
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce({ data: [] })
    render(<EligibilityConsent identity={identity} />)
    fireEvent.change(screen.getByLabelText('Synthetic reference'), { target: { value: 'SYN-ADULT-TEST' } })
    fireEvent.click(screen.getByRole('button', { name: 'Find patient' }))
    await screen.findByText(/Patient:/)
    expect(screen.getByRole('button', { name: 'Record eligibility check' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Record consent/ })).toBeInTheDocument()
  })
})
