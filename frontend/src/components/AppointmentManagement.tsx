import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import type { StaffIdentity } from '../api/auth'
import { ApiError, apiFetch, apiFetchPage } from '../api/client'
import { displayKampala, kampalaToUtc } from '../api/appointmentTime'

export interface Appointment {
  id: string
  starts_at: string
  status: 'scheduled' | 'confirmed' | 'attended' | 'missed' | 'cancelled' | 'rescheduled'
  version: number
  episode_version: number
  supersedes_id: string | null
  superseded_by_id: string | null
  reminder_eligible: boolean
  reminder_hold_reason: string
}

interface AppointmentEvent {
  id: string; event_type: string; sequence: number; recorded_by: string; effective_at: string
  data: { reason: string | null; related_appointment_id: string | null }
}

export default function AppointmentManagement({ identity, episode, facilityId, onChanged }: {
  identity: StaffIdentity
  episode: { id: string; version: number; assignment_id: string | null; lifecycle: string }
  facilityId: string
  onChanged: () => Promise<void>
}) {
  const [rows, setRows] = useState<Appointment[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [filter, setFilter] = useState('')
  const [busy, setBusy] = useState(true)
  const [loaded, setLoaded] = useState(false)
  const [message, setMessage] = useState('')
  const [time, setTime] = useState('')
  const [reason, setReason] = useState('')
  const [attendance, setAttendance] = useState('')
  const [effective, setEffective] = useState('')
  const [action, setAction] = useState<{ row: Appointment; kind: string } | null>(null)
  const [history, setHistory] = useState<{ id: string; events: AppointmentEvent[] } | null>(null)
  const retry = useRef<{ input: string; key: string } | null>(null)
  const can = (permission: string) => identity.permissions.includes(`appointment.${permission}`)
  const scope = `facility_id=${encodeURIComponent(facilityId)}`
  const listPath = `/appointments?${scope}&episode_id=${encodeURIComponent(episode.id)}${filter ? `&status=${filter}` : ''}`

  const load = useCallback(async (next?: string, signal?: AbortSignal) => {
    const result = await apiFetchPage<Appointment>(`${listPath}${next ? `&cursor=${encodeURIComponent(next)}` : ''}`, { signal })
    if (signal?.aborted) return
    setRows((previous) => next ? [...new Map([...previous, ...result.data].map((row) => [row.id, row])).values()] : result.data)
    setCursor(result.page.next_cursor)
    setLoaded(true)
  }, [listPath])

  useEffect(() => {
    const controller = new AbortController()
    void Promise.resolve().then(async () => {
      setBusy(true); setLoaded(false); setRows([]); setAction(null); setHistory(null)
      await load(undefined, controller.signal)
    }).catch(() => {
      if (!controller.signal.aborted) setMessage('Appointments are unavailable.')
    }).finally(() => { if (!controller.signal.aborted) setBusy(false) })
    return () => controller.abort()
  }, [load, episode.lifecycle])

  async function refresh(next?: string) {
    setBusy(true); setMessage('')
    try { await onChanged(); await load(next); setAction(null); setHistory(null) }
    catch { setRows([]); setLoaded(false); setMessage('Appointments are unavailable.') }
    finally { setBusy(false) }
  }

  async function write(path: string, body: unknown, version: number) {
    const input = JSON.stringify({ path, body })
    const key = retry.current?.input === input ? retry.current.key : crypto.randomUUID()
    retry.current = { input, key }
    setBusy(true); setMessage('')
    try {
      await apiFetch<Appointment>(`${path}?${scope}`, { method: 'POST', body, idempotencyKey: key, ifMatch: `"${version}"` })
      retry.current = null
      await onChanged()
      await load()
      setAction(null); setHistory(null); setTime(''); setReason(''); setAttendance(''); setEffective('')
      setMessage('Appointment saved. History is preserved; reminders remain held.')
    } catch (error) {
      if (error instanceof ApiError && [401, 403, 404].includes(error.status)) {
        setRows([]); setLoaded(false); setAction(null); setHistory(null)
        setMessage('Appointment access is unavailable.')
      } else if (error instanceof ApiError && [409, 412].includes(error.status)) {
        setMessage('The booking changed or conflicts. Refresh appointments before retrying.')
      } else setMessage('Appointment could not be saved. Retry with the same details.')
    } finally { setBusy(false) }
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    try {
      if (!action) {
        await write('/appointments', { episode_facility_assignment_id: episode.assignment_id,
          starts_at: kampalaToUtc(time) }, episode.version)
      } else {
        const body = action.kind === 'reschedule' ? { starts_at: kampalaToUtc(time), reason }
          : action.kind === 'attendance' ? { status: attendance, effective_at: kampalaToUtc(effective), ...(reason.trim() ? { reason } : {}) }
            : { reason }
        await write(`/appointments/${action.row.id}/${action.kind}`, body, action.row.version)
      }
    } catch { setMessage('Enter a valid Africa/Kampala date and time.') }
  }

  function choose(row: Appointment, kind: string) {
    setAction({ row, kind }); setTime(''); setReason(''); setAttendance(''); setEffective(''); setMessage('')
  }

  async function showHistory(row: Appointment) {
    setBusy(true); setMessage('')
    try {
      const events = await apiFetch<AppointmentEvent[]>(`/appointments/${row.id}/events?${scope}`)
      setHistory({ id: row.id, events })
    } catch { setHistory(null); setMessage('Appointment history is unavailable.') }
    finally { setBusy(false) }
  }

  return <section className="appointment-panel" aria-labelledby="appointments-title">
    <h3 id="appointments-title">ANC appointments</h3>
    <p>Synthetic records only. Times are entered and displayed in Africa/Kampala. D06 remains
      unresolved: no automatic clinical schedule, missed-visit determination or reminders.</p>
    <label>Appointment status filter
      <select value={filter} disabled={busy} onChange={(event) => setFilter(event.target.value)}>
        <option value="">All statuses</option>
        {['scheduled', 'confirmed', 'attended', 'missed', 'cancelled', 'rescheduled'].map((state) => <option key={state}>{state}</option>)}
      </select>
    </label>
    <button type="button" disabled={busy} onClick={() => refresh()}>Refresh appointments</button>
    {message && <p role="status">{message}</p>}
    {busy && <p role="status">Loading appointments…</p>}
    {loaded && (action || (can('schedule') && episode.lifecycle === 'ongoing' && episode.assignment_id)) &&
      <form className="appointment-form" onSubmit={submit}>
        <h4>{action ? `${action.kind} booking ${action.row.id}` : 'Schedule appointment'}</h4>
        {(!action || action.kind === 'reschedule') && <label>Appointment time (Africa/Kampala)
          <input type="datetime-local" value={time} onChange={(event) => setTime(event.target.value)} required />
        </label>}
        {action?.kind === 'attendance' && <>
          <label>Staff-recorded attendance
            <select value={attendance} onChange={(event) => setAttendance(event.target.value)} required>
              <option value="">Select recorded outcome</option><option value="attended">Attended</option><option value="missed">Missed — staff recorded</option>
            </select>
          </label>
          <label>Recorded effective time (Africa/Kampala)
            <input type="datetime-local" value={effective} onChange={(event) => setEffective(event.target.value)} required />
          </label>
        </>}
        {action && <label>Appointment reason
          <textarea value={reason} onChange={(event) => setReason(event.target.value)} maxLength={1000}
            required={['cancel', 'reschedule'].includes(action.kind)} />
        </label>}
        <button disabled={busy} type="submit">{action ? 'Save appointment action' : 'Save scheduled appointment'}</button>
        {action && <button type="button" disabled={busy} onClick={() => setAction(null)}>Close action</button>}
      </form>}
    {loaded && rows.length === 0 && <p>No appointments in this selection.</p>}
    <div className="appointment-list">
      {rows.map((row) => <article key={row.id} aria-label={`Booking ${row.id}`}>
        <h4>{displayKampala(row.starts_at)}</h4><p>Status: {row.status} · Version: {row.version}</p>
        <p>Booking: {row.id}</p><p>Reminder held: {row.reminder_hold_reason}</p>
        {row.supersedes_id && <p>Replaces booking {row.supersedes_id}; confirmation is independent.</p>}
        {row.superseded_by_id && <p>Replacement booking: {row.superseded_by_id}</p>}
        <div className="appointment-actions">
          <button type="button" disabled={busy} onClick={() => showHistory(row)}>View appointment history</button>
          {row.status === 'scheduled' && can('confirm') && episode.lifecycle === 'ongoing' &&
            <button type="button" disabled={busy} onClick={() => write(`/appointments/${row.id}/confirm`, {}, row.version)}>Confirm appointment</button>}
          {['scheduled', 'confirmed'].includes(row.status) && <>
            {can('reschedule') && episode.lifecycle === 'ongoing' && <button type="button" disabled={busy} onClick={() => choose(row, 'reschedule')}>Reschedule appointment</button>}
            {can('cancel') && <button type="button" disabled={busy} onClick={() => choose(row, 'cancel')}>Cancel appointment</button>}
          </>}
          {row.status === 'confirmed' && can('attend') && episode.lifecycle === 'ongoing' &&
            <button type="button" disabled={busy} onClick={() => choose(row, 'attendance')}>Record attendance</button>}
        </div>
      </article>)}
    </div>
    {cursor && <button type="button" disabled={busy} onClick={() => refresh(cursor)}>Load more appointments</button>}
    {history && <article><h4>History for booking {history.id}</h4>
      <ol>{history.events.map((event) => <li key={event.id}>
        {event.sequence}. {event.event_type} — {displayKampala(event.effective_at)}
        <p>Recorded by {event.recorded_by}</p>{event.data.reason && <p>{event.data.reason}</p>}
        {event.data.related_appointment_id && <p>Related booking: {event.data.related_appointment_id}</p>}
      </li>)}</ol>
    </article>}
  </section>
}
