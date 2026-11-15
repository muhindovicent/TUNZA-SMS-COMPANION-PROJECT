import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { ApiError, apiFetch } from '../api/client'
import type { StaffIdentity } from '../api/auth'

interface DatingRevision {
  id: string
  episode_id: string
  basis: string
  estimated_due_date: string
  reason: string
  recorded_by: string
  created_at: string
  revision_sequence: number
  supersedes_id: string | null
  parent_version: number
}

interface PregnancyEpisode {
  id: string
  patient_id: string
  facility_id: string
  lifecycle: string
  communication_state: string
  version: number
  dating_summary: DatingRevision | null
}

interface SelectedPatient {
  id: string
  facility_id: string
  local_reference: string
}

export default function PregnancyManagement({ identity, patient }: {
  identity: StaffIdentity
  patient: SelectedPatient
}) {
  const [episodes, setEpisodes] = useState<PregnancyEpisode[]>([])
  const [episode, setEpisode] = useState<PregnancyEpisode | null>(null)
  const [history, setHistory] = useState<DatingRevision[]>([])
  const [busy, setBusy] = useState(true)
  const [loaded, setLoaded] = useState(false)
  const [message, setMessage] = useState('')
  const [basis, setBasis] = useState('')
  const [dueDate, setDueDate] = useState('')
  const [reason, setReason] = useState('')
  const retry = useRef<{ input: string; key: string } | null>(null)
  const creationKey = useRef(crypto.randomUUID())
  const canRead = identity.permissions.includes('pregnancy.read')
  const canCreate = identity.permissions.includes('pregnancy.create')
  const canDate = identity.permissions.includes('pregnancy.date')
  const facilityQuery = `facility_id=${encodeURIComponent(patient.facility_id)}`

  const loadDetail = useCallback(async (id: string, signal?: AbortSignal) => {
    const path = `/pregnancy-episodes/${encodeURIComponent(id)}`
    const [detail, revisions] = await Promise.all([
      apiFetch<PregnancyEpisode>(`${path}?${facilityQuery}`, { signal }),
      apiFetch<DatingRevision[]>(`${path}/dating-revisions?${facilityQuery}`, { signal }),
    ])
    if (!signal?.aborted) {
      setEpisode(detail)
      setHistory(revisions)
    }
  }, [facilityQuery])

  const loadEpisodes = useCallback(async (signal?: AbortSignal) => {
    const rows = await apiFetch<PregnancyEpisode[]>(
      `/pregnancy-episodes?patient_id=${encodeURIComponent(patient.id)}&${facilityQuery}`, { signal },
    )
    if (signal?.aborted) return
    setEpisodes(rows)
    const current = rows.find((row) => row.lifecycle === 'ongoing') ?? rows[0]
    if (current) await loadDetail(current.id, signal)
    else { setEpisode(null); setHistory([]) }
    if (!signal?.aborted) setLoaded(true)
  }, [patient.id, facilityQuery, loadDetail])

  useEffect(() => {
    const controller = new AbortController()
    if (canRead) {
      void Promise.resolve().then(() => loadEpisodes(controller.signal)).catch(() => {
        if (!controller.signal.aborted) setMessage('Pregnancy records are unavailable.')
      }).finally(() => {
        if (!controller.signal.aborted) setBusy(false)
      })
    }
    return () => controller.abort()
  }, [canRead, loadEpisodes])

  async function refresh() {
    setBusy(true)
    setMessage('')
    try { await loadEpisodes() }
    catch { setMessage('Pregnancy records are unavailable.') }
    finally { setBusy(false) }
  }

  async function createEpisode() {
    setBusy(true)
    setMessage('')
    try {
      await apiFetch<PregnancyEpisode>('/pregnancy-episodes', {
        method: 'POST', body: { patient_id: patient.id, facility_id: patient.facility_id },
        idempotencyKey: creationKey.current,
      })
      creationKey.current = crypto.randomUUID()
      await loadEpisodes()
      setMessage('Synthetic pregnancy episode created.')
    } catch (error) {
      setMessage(error instanceof ApiError && error.status === 409
        ? 'An episode already exists or the request conflicts. Refresh the episodes.'
        : 'Episode could not be created. Retry with the same details.')
    } finally { setBusy(false) }
  }

  async function addDatingRevision(event: FormEvent) {
    event.preventDefault()
    if (!episode) return
    const body = { basis: basis.trim(), estimated_due_date: dueDate, reason: reason.trim() }
    const input = JSON.stringify({ episode: episode.id, facility: patient.facility_id, body })
    const key = retry.current?.input === input ? retry.current.key : crypto.randomUUID()
    retry.current = { input, key }
    setBusy(true)
    setMessage('')
    try {
      await apiFetch<DatingRevision>(
        `/pregnancy-episodes/${encodeURIComponent(episode.id)}/dating-revisions?${facilityQuery}`, {
          method: 'POST', body, idempotencyKey: key, ifMatch: `"${episode.version}"`,
        },
      )
      retry.current = null
      await loadEpisodes()
      setBasis(''); setDueDate(''); setReason('')
      setMessage('Dating revision recorded. Previous revisions are preserved.')
    } catch (error) {
      if (error instanceof ApiError && error.status === 412) {
        setMessage('The episode changed. Refresh it before recording a correction.')
      } else {
        setMessage('Dating revision could not be recorded. Retry with the same details.')
      }
    } finally { setBusy(false) }
  }

  if (!canRead) return null
  return (
    <section className="pregnancy-panel" aria-labelledby="pregnancy-title">
      <h2 id="pregnancy-title">Pregnancy episodes — {patient.local_reference}</h2>
      <p>Synthetic data only. D05 clinical dating rules remain unresolved. Recorded dates and
        methods do not authorize messaging or schedule ANC appointments.</p>
      <button type="button" onClick={refresh} disabled={busy}>Refresh episodes</button>
      {canCreate && loaded && !episodes.some((row) => row.lifecycle === 'ongoing') &&
        <button type="button" onClick={createEpisode} disabled={busy}>Create pregnancy episode</button>}
      {episodes.length > 0 && <label>Pregnancy episode
        <select value={episode?.id ?? ''} disabled={busy} onChange={async (event) => {
          setBusy(true); setEpisode(null); setHistory([])
          try { await loadDetail(event.target.value) }
          catch { setMessage('Episode is unavailable.') }
          finally { setBusy(false) }
        }}>
          {episodes.map((row) => <option key={row.id} value={row.id}>{row.lifecycle} — {row.id}</option>)}
        </select>
      </label>}
      {message && <p role="status">{message}</p>}
      {busy && <p role="status">Loading pregnancy records…</p>}
      {episode && <>
        <dl><dt>Lifecycle</dt><dd>{episode.lifecycle}</dd>
          <dt>Communication</dt><dd>{episode.communication_state}</dd>
          <dt>Version</dt><dd>{episode.version}</dd></dl>
        {canDate && episode.lifecycle === 'ongoing' && <form className="dating-form" onSubmit={addDatingRevision}>
          <h3>Record dating revision</h3>
          <label>Recorded method
            <input value={basis} onChange={(event) => setBasis(event.target.value)} required maxLength={64} />
          </label>
          <label>Estimated due date
            <input type="date" value={dueDate} onChange={(event) => setDueDate(event.target.value)} required />
          </label>
          <label>Reason for revision
            <textarea value={reason} onChange={(event) => setReason(event.target.value)} required maxLength={1000} />
          </label>
          <button type="submit" disabled={busy}>Record dating revision</button>
        </form>}
        <h3>Dating history</h3>
        {history.length === 0 ? <p>No dating revisions recorded.</p> : <ol className="dating-history">
          {history.map((row) => <li key={row.id}>
            <strong>Revision {row.revision_sequence}: {row.estimated_due_date}</strong>
            <p>Method: {row.basis}</p><p>Reason: {row.reason}</p>
            <p>Recorded by {row.recorded_by} at {row.created_at}</p>
            <p>{row.supersedes_id ? `Supersedes ${row.supersedes_id}` : 'Initial revision'}</p>
          </li>)}
        </ol>}
      </>}
    </section>
  )
}
