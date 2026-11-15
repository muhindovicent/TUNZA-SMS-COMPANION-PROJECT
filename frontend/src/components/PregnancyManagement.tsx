import { useState, useCallback } from 'react'
import type { FormEvent } from 'react'
import { apiFetch } from '../api/client'
import type { StaffIdentity } from '../api/auth'

interface PregnancyEpisode {
  id: string
  patient_id: string
  facility_id: string
  lifecycle: string
  communication_state: string
  version: number
  assignment_id: string | null
  dating_summary: {
    revision_id: string
    basis: string
    estimated_due_date: string
    reason: string
    revision_sequence: number
    supersedes_id: string | null
  } | null
}

interface DatingRevision {
  id: string
  episode_id: string
  basis: string
  estimated_due_date: string
  reason: string
  revision_sequence: number
  supersedes_id: string | null
  version: number
}

export default function PregnancyManagement({
  identity,
  patientId,
}: {
  identity: StaffIdentity
  patientId: string
}) {
  const [facility, setFacility] = useState(identity.facilities[0]?.id ?? '')
  const [episode, setEpisode] = useState<PregnancyEpisode | null>(null)
  const [revisions, setRevisions] = useState<DatingRevision[]>([])
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [basis, setBasis] = useState('lmp')
  const [dueDate, setDueDate] = useState('')
  const [reason, setReason] = useState('')
  const canCreate = identity.permissions.includes('pregnancy.create')
  const canDate = identity.permissions.includes('pregnancy.date')

  const basisOptions = [
    { value: 'lmp', label: 'Last menstrual period (LMP)' },
    { value: 'ultrasound', label: 'Ultrasound dating scan' },
    { value: 'clinical', label: 'Clinical assessment' },
    { value: 'fundal_height', label: 'Fundal height measurement' },
  ]

  const createEpisode = useCallback(
    async (event: FormEvent) => {
      event.preventDefault()
      if (!facility) return
      const body = { patient_id: patientId, facility_id: facility }
      const key = crypto.randomUUID()
      setEpisode(null)
      setRevisions([])
      setMessage('')
      setBusy(true)
      try {
        const result = await apiFetch<PregnancyEpisode>('/pregnancy-episodes', {
          method: 'POST',
          body,
          idempotencyKey: key,
        })
        setEpisode(result)
        setMessage('Synthetic pregnancy episode created.')
      } catch {
        setMessage('Episode could not be created. Retry with the same details.')
      } finally {
        setBusy(false)
      }
    },
    [facility, patientId]
  )

  const loadEpisode = useCallback(
    async (episodeId: string) => {
      setBusy(true)
      try {
        const result = await apiFetch<PregnancyEpisode>(
          `/pregnancy-episodes/${encodeURIComponent(episodeId)}?facility_id=${encodeURIComponent(facility)}`
        )
        setEpisode(result)
        setMessage('Pregnancy episode loaded.')
      } catch {
        setMessage('Episode is unavailable.')
      } finally {
        setBusy(false)
      }
    },
    [facility]
  )

  const addDatingRevision = useCallback(
    async (event: FormEvent) => {
      event.preventDefault()
      if (!episode) return
      const body = { basis, estimated_due_date: dueDate, reason: reason.trim() }
      const key = crypto.randomUUID()
      setBusy(true)
      try {
        const result = await apiFetch<DatingRevision>(
          `/pregnancy-episodes/${encodeURIComponent(episode.id)}/dating-revisions?facility_id=${encodeURIComponent(facility)}`,
          {
            method: 'POST',
            body,
            idempotencyKey: key,
          }
        )
        setRevisions((prev) => [...prev, result])
        setMessage('Dating revision recorded.')
        setBasis('lmp')
        setDueDate('')
        setReason('')
        await loadEpisode(episode.id)
      } catch {
        setMessage('Dating revision could not be recorded.')
      } finally {
        setBusy(false)
      }
    },
    [episode, basis, dueDate, reason, facility, loadEpisode]
  )

  return (
    <section className="content-width" aria-labelledby="pregnancy-title">
      <h1 id="pregnancy-title">Pregnancy episode management</h1>
      <p>
        Synthetic patient data only. Dating revisions are append-only; corrections create new
        revisions and preserve prior history.
      </p>
      {canCreate && !episode && (
        <form onSubmit={createEpisode}>
          <label>Facility
            <select
              value={facility}
              onChange={(event) => setFacility(event.target.value)}
              required
            >
              <option value="">Select facility</option>
              {identity.facilities.map((item) => (
                <option key={item.id} value={item.id}>{item.name}</option>
              ))}
            </select>
          </label>
          <button type="submit" disabled={busy || !facility}>
            Create pregnancy episode
          </button>
        </form>
      )}
      {episode && (
        <>
          <article>
            <h2>Episode</h2>
            <p>Patient: {patientId}</p>
            <p>Lifecycle: {episode.lifecycle}</p>
            <p>Communication: {episode.communication_state}</p>
            <p>Version: {episode.version}</p>
            {episode.dating_summary && (
              <div>
                <p>Latest dating revision:</p>
                <ul>
                  <li>Basis: {episode.dating_summary.basis}</li>
                  <li>Estimated due date: {episode.dating_summary.estimated_due_date}</li>
                  <li>Reason: {episode.dating_summary.reason}</li>
                  <li>Sequence: {episode.dating_summary.revision_sequence}</li>
                </ul>
              </div>
            )}
          </article>
          {canDate && (
            <form onSubmit={addDatingRevision}>
              <h3>Add dating revision</h3>
              <label>Basis
                <select value={basis} onChange={(event) => setBasis(event.target.value)} required>
                  {basisOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>Estimated due date
                <input
                  type="datetime-local"
                  value={dueDate}
                  onChange={(event) => setDueDate(event.target.value)}
                  required
                />
              </label>
              <label>Reason
                <textarea
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  required
                  maxLength={1000}
                />
              </label>
              <button type="submit" disabled={busy}>
                Record dating revision
              </button>
            </form>
          )}
          {revisions.length > 0 && (
            <article>
              <h3>Dating history</h3>
              <ul>
                {revisions.map((rev) => (
                  <li key={rev.id}>
                    Sequence {rev.revision_sequence} — {rev.basis} — {rev.estimated_due_date} —{' '}
                    {rev.reason}{' '}
                    {rev.supersedes_id ? `(supersedes ${rev.supersedes_id})` : '(initial)'}
                  </li>
                ))}
              </ul>
            </article>
          )}
          {message && <p role="status">{message}</p>}
        </>
      )}
    </section>
  )
}
