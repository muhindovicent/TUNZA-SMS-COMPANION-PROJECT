import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import type { StaffIdentity } from '../api/auth'
import { ApiError, apiFetch } from '../api/client'
import { displayKampala } from '../api/appointmentTime'

interface CompletionRecord {
  id: string; completed_on: string; reason: string; sequence: number; parent_version: number
  supersedes_id: string | null; recorded_by: string; recorded_at: string; closed_at: string
  cancelled_appointment_count: number; lifecycle: 'completed'; communication_state: string
}

interface CompletionInput {
  completed_on: string; approved_outcome_fields: Record<string, never>; reason: string
  supersedes_id?: string
}

export default function PregnancyCompletion({ identity, facilityId, episode, onChanged }: {
  identity: StaffIdentity; facilityId: string
  episode: { id: string; version: number; lifecycle: string; communication_state: string;
    assignment_id: string | null; completion_record_id?: string | null }
  onChanged: () => Promise<void>
}) {
  const permitted = identity.permissions.includes('pregnancy.complete')
  const [history, setHistory] = useState<CompletionRecord[]>([])
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(true)
  const [correcting, setCorrecting] = useState(false)
  const [completedOn, setCompletedOn] = useState('')
  const [reason, setReason] = useState('')
  const [message, setMessage] = useState('')
  const [draft, setDraft] = useState<{ body: CompletionInput; version: number } | null>(null)
  const retry = useRef<{ input: string; key: string } | null>(null)
  const path = `/pregnancy-episodes/${encodeURIComponent(episode.id)}/completion?facility_id=${encodeURIComponent(facilityId)}`

  const load = useCallback(async (signal?: AbortSignal) => {
    const records = await apiFetch<CompletionRecord[]>(path, { signal })
    if (!signal?.aborted) { setHistory(records); setLoaded(true) }
  }, [path])

  useEffect(() => {
    const controller = new AbortController()
    if (permitted) {
      void Promise.resolve().then(() => load(controller.signal)).catch(() => {
        if (!controller.signal.aborted) setMessage('Completion history is unavailable.')
      }).finally(() => { if (!controller.signal.aborted) setBusy(false) })
    }
    return () => controller.abort()
  }, [permitted, load])

  async function refresh() {
    setBusy(true); setMessage(''); setDraft(null)
    try { await onChanged(); await load(); setCorrecting(false) }
    catch { setHistory([]); setLoaded(false); setMessage('Completion access is unavailable.') }
    finally { setBusy(false) }
  }

  function preview(event: FormEvent) {
    event.preventDefault()
    if (!episode.assignment_id) return
    const body: CompletionInput = { completed_on: completedOn, approved_outcome_fields: {}, reason: reason.trim() }
    if (correcting && episode.completion_record_id) body.supersedes_id = episode.completion_record_id
    setDraft({ body, version: episode.version })
    setMessage('')
  }

  async function confirm() {
    if (!draft) return
    const input = JSON.stringify({ path, body: draft.body })
    const key = retry.current?.input === input ? retry.current.key : crypto.randomUUID()
    retry.current = { input, key }
    setBusy(true); setMessage('')
    try {
      const record = await apiFetch<CompletionRecord>(path, {
        method: 'POST', body: draft.body, idempotencyKey: key, ifMatch: `"${draft.version}"`,
      })
      retry.current = null
      setHistory((previous) => [record, ...previous.filter((row) => row.id !== record.id)])
      setDraft(null); setCorrecting(false); setCompletedOn(''); setReason('')
      try { await onChanged(); await load() }
      catch { setMessage('Completion saved; refresh the pregnancy to reload its latest state.'); return }
      setMessage('Completion saved. History is preserved; pregnancy messaging remains ineligible.')
    } catch (error) {
      if (error instanceof ApiError && [409, 412].includes(error.status)) {
        setMessage('The pregnancy changed or conflicts. Refresh before confirming again.')
      } else if (error instanceof ApiError && [401, 403, 404].includes(error.status)) {
        setHistory([]); setLoaded(false); setDraft(null); setMessage('Completion access is unavailable.')
      } else setMessage('Completion could not be saved. Retry the same confirmed details.')
    } finally { setBusy(false) }
  }

  if (!permitted) return null
  const current = history.find((row) => row.id === episode.completion_record_id)
  return <section className="completion-panel" aria-labelledby="pregnancy-completion-title">
    <h3 id="pregnancy-completion-title">Pregnancy completion</h3>
    <p>Synthetic records only. No clinical outcome fields are approved under D02/D05, and no
      postnatal enrolment or SMS dispatch is created.</p>
    <button type="button" disabled={busy} onClick={refresh}>Refresh completion history</button>
    {busy && <p role="status">Loading completion history…</p>}
    {message && <p role="status">{message}</p>}
    {loaded && episode.assignment_id && !draft && (episode.lifecycle === 'ongoing' || correcting) &&
      <form className="completion-form" onSubmit={preview}>
        <h4>{correcting ? 'Append completion correction' : 'Complete pregnancy episode'}</h4>
        <label>Staff-recorded completion date
          <input type="date" required value={completedOn} onChange={(event) => setCompletedOn(event.target.value)} />
        </label>
        <label>Completion reason
          <textarea required maxLength={1000} value={reason} onChange={(event) => setReason(event.target.value)} />
        </label>
        <button type="submit" disabled={busy}>Review completion</button>
        {correcting && <button type="button" disabled={busy} onClick={() => setCorrecting(false)}>Cancel correction</button>}
      </form>}
    {draft && <div role="group" aria-label="Confirm pregnancy completion" className="completion-confirmation">
      <h4>{draft.body.supersedes_id ? 'Confirm append-only correction' : 'Confirm lifecycle closure'}</h4>
      <p>Completion date: {draft.body.completed_on}</p><p>Reason: {draft.body.reason}</p>
      <p>{draft.body.supersedes_id
        ? 'This appends a correction. The episode stays completed, and the original closure instant and appointment history remain unchanged.'
        : 'This completes the episode and cancels future scheduled or confirmed bookings for its current facility responsibility. Historical and attended/missed records remain unchanged.'}</p>
      {episode.communication_state === 'withdrawn' && <p>The existing withdrawn state will be preserved.</p>}
      <button type="button" disabled={busy} onClick={confirm}>Confirm pregnancy completion</button>
      <button type="button" disabled={busy} onClick={() => setDraft(null)}>Back to completion details</button>
    </div>}
    {loaded && current && episode.lifecycle === 'completed' && !correcting && !draft &&
      <button type="button" disabled={busy} onClick={() => {
        setCorrecting(true); setCompletedOn(current.completed_on); setReason('')
      }}>Record completion correction</button>}
    {loaded && history.length === 0 && <p>{episode.lifecycle === 'completed'
      ? 'This legacy completed episode has no recorded completion history.' : 'No completion records.'}</p>}
    {history.length > 0 && <ol className="completion-history">{history.map((row) => <li key={row.id}>
      <h4>Completion revision {row.sequence} — {row.completed_on}</h4>
      <p>{row.reason}</p><p>Recorded by {row.recorded_by} at {displayKampala(row.recorded_at)}</p>
      <p>Original lifecycle closure: {displayKampala(row.closed_at)}</p>
      <p>Future current bookings cancelled: {row.cancelled_appointment_count}</p>
      <p>{row.supersedes_id ? `Corrects completion ${row.supersedes_id}` : 'Initial completion'}</p>
    </li>)}</ol>}
  </section>
}
