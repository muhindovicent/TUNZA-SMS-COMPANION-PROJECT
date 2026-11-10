import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import PatientRegistry from './PatientRegistry'
import type { StaffIdentity } from '../api/auth'
import { apiFetch } from '../api/client'

vi.mock('../api/client', () => ({ apiFetch: vi.fn() }))

const identity: StaffIdentity = {
  user_id: 'user', display_name: 'Synthetic staff', state: 'active', bindings: [],
  permissions: ['patient.read', 'patient.register'],
  facilities: [{ id: 'facility', code: 'TEST', name: 'Synthetic clinic' }],
}
const patient = { id: 'patient', facility_id: 'facility', local_reference: 'SYN-ADULT-TEST', language: 'en', version: 1 }

describe('synthetic patient registry', () => {
  beforeEach(() => vi.resetAllMocks())

  it('registers, searches and requests detail using API controls', async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({ patient }).mockResolvedValueOnce([patient]).mockResolvedValueOnce(patient)
    render(<PatientRegistry identity={identity} />)
    fireEvent.change(screen.getByLabelText('Synthetic reference'), { target: { value: patient.local_reference } })
    fireEvent.click(screen.getByRole('button', { name: 'Register synthetic patient' }))
    await screen.findByText('Synthetic registration saved.')
    expect(vi.mocked(apiFetch).mock.calls[0][1]?.idempotencyKey).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Search assigned patients' }))
    await waitFor(() => expect(vi.mocked(apiFetch)).toHaveBeenCalledTimes(2))
    fireEvent.click(await screen.findByRole('button', { name: 'View detail' }))
    await screen.findByText('Patient detail loaded.')
    expect(vi.mocked(apiFetch).mock.calls[2][0]).toContain('/patients/patient?facility_id=facility')
  })

  it('hides registration without permission', () => {
    render(<PatientRegistry identity={{ ...identity, permissions: ['patient.read'] }} />)
    expect(screen.queryByRole('button', { name: 'Register synthetic patient' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Search assigned patients' })).toBeInTheDocument()
  })
})
