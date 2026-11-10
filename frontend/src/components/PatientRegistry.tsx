import { useState } from 'react'
import type { FormEvent } from 'react'
import { apiFetch } from '../api/client'
import type { StaffIdentity } from '../api/auth'

interface Patient {
  id: string
  facility_id: string
  local_reference: string
  language: string
  version: number
}

export default function PatientRegistry({ identity }: { identity: StaffIdentity }) {
  const [facility, setFacility] = useState(identity.facilities[0]?.id ?? '')
  const [reference, setReference] = useState('')
  const [language, setLanguage] = useState('en')
  const [patient, setPatient] = useState<Patient | null>(null)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [retry, setRetry] = useState<{ input: string; key: string } | null>(null)
  const canRead = identity.permissions.includes('patient.read')
  const canRegister = identity.permissions.includes('patient.register')

  async function register(event: FormEvent) {
    event.preventDefault()
    const body = { facility_id: facility, local_reference: reference, demographics: {}, language }
    const input = JSON.stringify(body)
    const key = retry?.input === input ? retry.key : crypto.randomUUID()
    setRetry({ input, key })
    setPatient(null)
    setMessage('')
    setBusy(true)
    try {
      const result = await apiFetch<{ patient: Patient }>('/patients', {
        method: 'POST', body, idempotencyKey: key,
      })
      setPatient(result.patient)
      setMessage('Synthetic registration saved.')
    } catch {
      setMessage('Registration could not be completed. Retry with the same details.')
    } finally {
      setBusy(false)
    }
  }

  async function search() {
    setPatient(null)
    setMessage('')
    setBusy(true)
    try {
      const result = await apiFetch<Patient[]>('/patients/search', {
        method: 'POST', body: { facility_id: facility, local_reference: reference },
      })
      if (result.length === 0) setMessage('No assigned synthetic record found.')
      else setPatient(result[0])
    } catch {
      setMessage('Search could not be completed.')
    } finally {
      setBusy(false)
    }
  }

  async function detail() {
    if (!patient) return
    setBusy(true)
    try {
      setPatient(await apiFetch<Patient>(
        `/patients/${encodeURIComponent(patient.id)}?facility_id=${encodeURIComponent(facility)}`,
      ))
      setMessage('Patient detail loaded.')
    } catch {
      setPatient(null)
      setMessage('Patient detail is unavailable.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="content-width" aria-labelledby="patient-registry-title">
      <h1 id="patient-registry-title">Synthetic patient registry</h1>
      <p>Synthetic adult records only. No real patient details or contacts.</p>
      <form onSubmit={register}>
        <label>Facility
          <select value={facility} onChange={(event) => { setFacility(event.target.value); setPatient(null) }} required>
            <option value="">Select facility</option>
            {identity.facilities.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
        </label>
        <label>Synthetic reference
          <input value={reference} onChange={(event) => { setReference(event.target.value); setPatient(null) }}
            placeholder="SYN-ADULT-EXAMPLE" required maxLength={64} />
        </label>
        {canRegister && <label>Language
          <select value={language} onChange={(event) => setLanguage(event.target.value)}>
            <option value="en">English</option><option value="lg">Luganda</option>
          </select>
        </label>}
        {canRegister && <button type="submit" disabled={busy || !facility}>Register synthetic patient</button>}
        {canRead && <button type="button" onClick={search} disabled={busy || !facility || !reference}>Search assigned patients</button>}
      </form>
      {message && <p role="status">{message}</p>}
      {patient && <article>
        <h2>Patient</h2><p>Reference: {patient.local_reference}</p><p>Language: {patient.language}</p>
        {canRead && <button type="button" onClick={detail} disabled={busy}>View detail</button>}
      </article>}
    </section>
  )
}
