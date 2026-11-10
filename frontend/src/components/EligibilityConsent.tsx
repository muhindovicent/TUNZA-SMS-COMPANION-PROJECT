import { useState } from 'react'
import type { FormEvent } from 'react'
import { apiFetch } from '../api/client'
import type { StaffIdentity } from '../api/auth'

interface EligibilityCheck {
  id: string
  patient_id: string
  facility_id: string
  method: string
  result: string
  checked_at: string
  version: number
}

interface ConsentEvent {
  id: string
  patient_id: string
  facility_id: string
  purpose: string
  decision: string
  method: string
  language: string
  effective_at: string
  recorded_by: string
  version: number
}

export default function EligibilityConsent({ identity }: { identity: StaffIdentity }) {
  const [facility, setFacility] = useState(identity.facilities[0]?.id ?? '')
  const [reference, setReference] = useState('')
  const [patient, setPatient] = useState<{ id: string; facility_id: string; local_reference: string } | null>(null)
  const [eligibility, setEligibility] = useState<EligibilityCheck | null>(null)
  const [consentEvents, setConsentEvents] = useState<ConsentEvent[]>([])
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState('uncertain')
  const [method, setMethod] = useState('documentary')
  const [consentDecision, setConsentDecision] = useState('grant')
  const [consentPurpose, setConsentPurpose] = useState('messaging')
  const [consentMethod, setConsentMethod] = useState('documentary')

  const canRecordEligibility = identity.permissions.includes('eligibility.record')
  const canRecordConsent = identity.permissions.includes('consent.record')
  const canWithdrawConsent = identity.permissions.includes('consent.withdraw')
  const canReadConsent = identity.permissions.includes('consent.read')

  async function findPatient() {
    setPatient(null)
    setEligibility(null)
    setConsentEvents([])
    setMessage('')
    setBusy(true)
    try {
      const result = await apiFetch<{ data: { patient: { id: string; facility_id: string; local_reference: string } }[] }>('/patients/search', {
        method: 'POST', body: { facility_id: facility, local_reference: reference },
      })
      if (!result) {
        setMessage('Search could not be completed.')
        setBusy(false)
        return
      }
      const patients = result.data
      if (patients.length === 0) {
        setMessage('No assigned synthetic record found.')
      } else {
        const found = patients[0].patient
        setPatient(found)
        await loadEligibility(found.id)
        await loadConsentEvents(found.id)
      }
    } catch {
      setMessage('Search could not be completed.')
    } finally {
      setBusy(false)
    }
  }

  async function loadEligibility(patientId: string) {
    try {
      const checks = await apiFetch<EligibilityCheck[]>(`/patients/${encodeURIComponent(patientId)}/eligibility?facility_id=${encodeURIComponent(facility)}`, {
        method: 'GET',
      })
      if (checks && checks.length > 0) setEligibility(checks[0])
    } catch {
      setEligibility(null)
    }
  }

  async function loadConsentEvents(patientId: string) {
    try {
      const events = await apiFetch<ConsentEvent[]>(`/patients/${encodeURIComponent(patientId)}/consent-events?facility_id=${encodeURIComponent(facility)}`, {
        method: 'GET',
      })
      setConsentEvents(events || [])
    } catch {
      setConsentEvents([])
    }
  }

  async function recordEligibility(event: FormEvent) {
    event.preventDefault()
    if (!patient) return
    setBusy(true)
    setMessage('')
    try {
      await apiFetch<EligibilityCheck>(`/patients/${encodeURIComponent(patient.id)}/eligibility?facility_id=${encodeURIComponent(facility)}`, {
        method: 'POST',
        body: { method, result, checked_at: new Date().toISOString() },
        idempotencyKey: crypto.randomUUID(),
      })
      await loadEligibility(patient.id)
      setMessage('Eligibility check recorded.')
    } catch {
      setMessage('Eligibility check could not be recorded.')
    } finally {
      setBusy(false)
    }
  }

  async function recordConsent(event: FormEvent) {
    event.preventDefault()
    if (!patient) return
    setBusy(true)
    setMessage('')
    try {
      await apiFetch<ConsentEvent>(`/patients/${encodeURIComponent(patient.id)}/consent-events?facility_id=${encodeURIComponent(facility)}`, {
        method: 'POST',
        body: { purpose: consentPurpose, decision: consentDecision, method: consentMethod, language: 'en' },
        idempotencyKey: crypto.randomUUID(),
      })
      await loadConsentEvents(patient.id)
      setMessage(`Consent ${consentDecision} recorded.`)
    } catch {
      setMessage('Consent could not be recorded.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="content-width" aria-labelledby="eligibility-consent-title">
      <h1 id="eligibility-consent-title">Eligibility &amp; Consent</h1>
      <p>Synthetic adult records only. No clinical policy values are implied.</p>
      <form onSubmit={findPatient}>
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
        <button type="submit" disabled={busy || !facility}>Find patient</button>
      </form>
      {message && <p role="status">{message}</p>}
      {patient && <article>
        <h2>Patient: {patient.local_reference}</h2>
        <p>Facility: {patient.facility_id}</p>

        {canRecordEligibility && <section>
          <h3>Eligibility assessment</h3>
          {eligibility ? (
            <article>
              <p>Result: {eligibility.result}</p>
              <p>Method: {eligibility.method}</p>
              <p>Checked at: {eligibility.checked_at}</p>
            </article>
          ) : (
            <form onSubmit={recordEligibility}>
              <label>Method
                <select value={method} onChange={(event) => setMethod(event.target.value)}>
                  <option value="documentary">Documentary</option>
                  <option value="verbal">Verbal</option>
                </select>
              </label>
              <label>Result
                <select value={result} onChange={(event) => setResult(event.target.value)}>
                  <option value="verified">Verified</option>
                  <option value="uncertain">Uncertain</option>
                  <option value="ineligible">Ineligible</option>
                </select>
              </label>
              <button type="submit" disabled={busy}>Record eligibility check</button>
            </form>
          )}
        </section>}

        {canRecordConsent && <section>
          <h3>Consent management</h3>
          <form onSubmit={recordConsent}>
            <label>Purpose
              <input value={consentPurpose} onChange={(event) => setConsentPurpose(event.target.value)} />
            </label>
            <label>Decision
              <select value={consentDecision} onChange={(event) => setConsentDecision(event.target.value)}>
                <option value="grant">Grant</option>
                <option value="withdraw">Withdraw</option>
                <option value="correct">Correct</option>
              </select>
            </label>
            <label>Method
              <select value={consentMethod} onChange={(event) => setConsentMethod(event.target.value)}>
                <option value="documentary">Documentary</option>
                <option value="verbal">Verbal</option>
              </select>
            </label>
            <button type="submit" disabled={busy || !canWithdrawConsent && consentDecision === 'withdraw'}>
              Record consent {consentDecision}
            </button>
          </form>
        </section>}

        {canReadConsent && consentEvents.length > 0 && <section>
          <h3>Consent history</h3>
          <ul>
            {consentEvents.map((event) => (
              <li key={event.id}>
                {event.decision} - {event.purpose} ({event.method}) at {event.effective_at}
              </li>
            ))}
          </ul>
        </section>}
      </article>}
    </section>
  )
}