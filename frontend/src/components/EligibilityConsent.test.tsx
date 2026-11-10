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
  beforeEach(() => vi.resetAllMocks())

  it('registers eligibility and records consent', async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce([{ patient: { id: 'patient', facility_id: 'facility', local_reference: 'SYN-ADULT-TEST' } }])
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([])
    render(<EligibilityConsent identity={identity} />)
    fireEvent.change(screen.getByLabelText('Synthetic reference'), { target: { value: 'SYN-ADULT-TEST' } })
    fireEvent.click(screen.getByRole('button', { name: 'Find patient' }))
    await screen.findByText(/Patient:/)
    fireEvent.change(screen.getByLabelText('Result'), { target: { value: 'verified' } })
    fireEvent.click(screen.getByRole('button', { name: 'Record eligibility check' }))
    await screen.findByText('Eligibility check recorded.')
    expect(vi.mocked(apiFetch).mock.calls.some((call) => call[0]?.includes('/patients/patient/eligibility'))).toBe(true)
  })
})