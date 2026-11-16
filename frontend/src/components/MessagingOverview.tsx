import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import type { StaffFacility, StaffIdentity } from '../api/auth'
import { ApiError, apiFetch, apiFetchPage } from '../api/client'
import { displayKampala, kampalaToUtc } from '../api/appointmentTime'

interface Occurrence {
  id: string; facility_id: string; kind: string; state: string; hold_reasons: string[]
  version: number; due_at: string; expires_at: string
}
interface Event { sequence: number; event_type: string; state: string; hold_reasons: string[]; created_at: string }
interface Policy { paused: boolean; version: number; code: string }
interface Budget { id: string; cap: string; reserved: string; unit_cost: string; period_end: string }

export default function MessagingOverview({ identity }: { identity: StaffIdentity }) {
  const can = (permission: string) => identity.permissions.includes(permission)
  const operations = !can('message.read') && can('message.ops')
  const [facilityOptions, setFacilityOptions] = useState<StaffFacility[]>(identity.facilities)
  const [facility, setFacility] = useState(operations ? '' : identity.facilities[0]?.id ?? '')
  const [state, setState] = useState('')
  const [rows, setRows] = useState<Occurrence[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [selected, setSelected] = useState<Occurrence | null>(null)
  const [events, setEvents] = useState<Event[]>([])
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [refreshRequired, setRefreshRequired] = useState(false)
  const [episode, setEpisode] = useState('')
  const [kind, setKind] = useState('weekly')
  const [week, setWeek] = useState('')
  const [appointment, setAppointment] = useState('')
  const [reminder, setReminder] = useState('')
  const [template, setTemplate] = useState('')
  const [due, setDue] = useState('')
  const [expiry, setExpiry] = useState('')
  const [control, setControl] = useState<Policy | null>(null)
  const [budgets, setBudgets] = useState<Budget[]>([])
  const [code, setCode] = useState('')
  const [periodStart, setPeriodStart] = useState('')
  const [periodEnd, setPeriodEnd] = useState('')
  const [cap, setCap] = useState('')
  const [rate, setRate] = useState('')
  const [encoding, setEncoding] = useState('gsm7')
  const [units, setUnits] = useState('')
  const retry = useRef<{ input: string; key: string } | null>(null)
  const scope = facility ? `facilities/${encodeURIComponent(facility)}` : 'global'
  const query = `${operations ? 'view=operations' : ''}${facility ? `&facility_id=${encodeURIComponent(facility)}` : ''}`
  const path = `/message-occurrences?${query}${state ? `&state=${state}` : ''}`
  const allowed = operations || Boolean(facility)

  useEffect(() => {
    if (!operations || !identity.permissions.includes('facility.read')) return
    const controller = new AbortController()
    void apiFetch<StaffFacility[]>('/facilities', { signal: controller.signal }).then((options) => {
      if (!controller.signal.aborted) setFacilityOptions(options)
    }).catch(() => { if (!controller.signal.aborted) setMessage('Facility options unavailable.') })
    return () => controller.abort()
  }, [operations, identity.permissions])

  const load = useCallback(async (next?: string, signal?: AbortSignal) => {
    const [page, policy, budgetRows] = await Promise.all([
      apiFetchPage<Occurrence>(`${path}${next ? `&cursor=${encodeURIComponent(next)}` : ''}`, { signal }),
      identity.permissions.includes('messaging.control') ? apiFetch<Policy>(`/messaging/${scope}/pause`, { signal }) : null,
      identity.permissions.includes('messaging.control') ? apiFetch<Budget[]>(`/messaging/${scope}/budgets`, { signal }) : [],
    ])
    if (!signal?.aborted) {
      setRows((previous) => next ? [...previous, ...page.data] : page.data)
      setCursor(page.page.next_cursor); setControl(policy)
      setBudgets(budgetRows)
    }
  }, [path, scope, identity.permissions])

  useEffect(() => {
    const controller = new AbortController()
    void Promise.resolve().then(async () => {
      setSelected(null); setEvents([]); setRows([]); setMessage('')
      if (!allowed) return
      setBusy(true)
      await load(undefined, controller.signal)
    }).catch(() => { if (!controller.signal.aborted) { setRows([]); setControl(null); setBudgets([]); setMessage('Messaging overview unavailable.') } })
      .finally(() => { if (!controller.signal.aborted) setBusy(false) })
    return () => controller.abort()
  }, [allowed, load])

  async function refresh() {
    setBusy(true); setSelected(null); setEvents([]); setMessage('')
    try { await load(); setRefreshRequired(false) }
    catch { setRows([]); setControl(null); setBudgets([]); setMessage('Messaging overview unavailable.') }
    finally { setBusy(false) }
  }

  async function open(row: Occurrence) {
    setBusy(true); setSelected(null); setEvents([]); setMessage('')
    try {
      const history = await apiFetch<Event[]>(`/message-occurrences/${row.id}/events?${query}`)
      setSelected(row); setEvents(history)
    } catch { setMessage('Scheduling history unavailable.') }
    finally { setBusy(false) }
  }

  async function write(url: string, body: unknown, version?: number) {
    if (busy || refreshRequired) return
    const input = JSON.stringify({ url, body })
    const key = retry.current?.input === input ? retry.current.key : crypto.randomUUID()
    retry.current = { input, key }
    setBusy(true); setMessage('')
    let receipt: { reserved?: boolean; hold_reasons?: string[]; segments?: number; encoding?: string }
    try {
      receipt = await apiFetch(url, { method: 'POST', body, idempotencyKey: key,
        ...(version === undefined ? {} : { ifMatch: `"${version}"` }) })
    } catch (error) {
      setMessage(error instanceof ApiError && [409, 412, 428].includes(error.status)
        ? 'Scheduling state changed. Refresh before retrying.' : 'Scheduling action unavailable. Retry the same details.')
      setBusy(false)
      return
    }
    retry.current = null
    setEpisode(''); setWeek(''); setAppointment(''); setReminder(''); setDue(''); setExpiry('')
    setCode(''); setSelected(null); setEvents([])
    try {
      await load()
      setMessage(receipt.reserved === false ? `Synthetic capacity held: ${receipt.hold_reasons?.join(', ')}.`
        : receipt.reserved === true ? `Synthetic capacity reserved: ${receipt.segments} ${receipt.encoding} segments. No dispatch authorization.`
          : 'Synthetic scheduling action saved. No SMS submission is authorized.')
    }
    catch { setRows([]); setControl(null); setBudgets([]); setRefreshRequired(true) }
    finally { setBusy(false) }
  }

  async function plan(event: FormEvent) {
    event.preventDefault()
    try {
      await write(`/message-occurrences?facility_id=${encodeURIComponent(facility)}`, {
        episode_id: episode, kind, due_at: kampalaToUtc(due), expires_at: kampalaToUtc(expiry),
        ...(kind === 'weekly' ? { gestational_week: Number(week) } : { appointment_id: appointment, reminder_code: reminder }),
        ...(template ? { template_id: template } : {}),
      })
    } catch { setMessage('Enter valid explicit fixture times in Africa/Kampala.') }
  }

  async function budget(event: FormEvent) {
    event.preventDefault()
    try {
      await write(`/messaging/${scope}/budgets`, { period_start: kampalaToUtc(periodStart),
        period_end: kampalaToUtc(periodEnd), cap, unit_cost: rate, currency: 'SYN', synthetic_only: true })
    } catch { setMessage('Enter valid explicit synthetic budget periods.') }
  }

  const blocked = busy || refreshRequired
  return <section className="content-width messaging-overview" aria-labelledby="messaging-title">
    <h1 id="messaging-title">Durable messaging — synthetic scheduling</h1>
    <p role="note">D05, D06, D07, D08, D14, D14a and D15 are unresolved. Candidates stay held;
      fixture approval and capacity reservations do not authorize SMS submission.</p>
    {operations && <p>Operations metadata only. Patient identities and message bodies are unavailable.</p>}
    <div className="content-filters">
      <label>Messaging facility<select value={facility} disabled={busy} onChange={(event) => setFacility(event.target.value)}>
        {operations && <option value="">All authorized operations / global controls</option>}
        {!operations && !facility && <option value="">Select an authorized facility</option>}
        {facilityOptions.map((row) => <option key={row.id} value={row.id}>{row.code}</option>)}
      </select></label>
      <label>Occurrence status<select value={state} disabled={busy} onChange={(event) => setState(event.target.value)}>
        <option value="">All authorized statuses</option>{['held', 'suppressed', 'cancelled', 'expired'].map((value) => <option key={value}>{value}</option>)}
      </select></label>
    </div>
    <button type="button" disabled={busy || !allowed} onClick={refresh}>Refresh scheduling</button>
    {busy && <p role="status">Loading scheduling…</p>}
    {message && <p role="status">{message}</p>}
    {refreshRequired && <p role="status">Action saved. Refresh scheduling to recover the view before another write.</p>}
    {can('message.plan') && facility && <form className="content-form" onSubmit={plan}>
      <h2>Plan a held fixture candidate</h2>
      <label>Assigned episode ID<input required value={episode} onChange={(event) => setEpisode(event.target.value)} /></label>
      <label>Candidate kind<select value={kind} onChange={(event) => setKind(event.target.value)}>
        <option value="weekly">Weekly fixture index</option><option value="appointment_reminder">Confirmed appointment reminder</option>
      </select></label>
      {kind === 'weekly' ? <label>Synthetic gestational index<input required type="number" min={0} max={2147483647} value={week} onChange={(event) => setWeek(event.target.value)} /></label>
        : <><label>Confirmed appointment ID<input required value={appointment} onChange={(event) => setAppointment(event.target.value)} /></label>
          <label>Synthetic reminder code<input required pattern="SYN-[A-Z0-9-]{2,60}" value={reminder} onChange={(event) => setReminder(event.target.value)} /></label></>}
      <label>Fixture template ID (optional)<input value={template} onChange={(event) => setTemplate(event.target.value)} /></label>
      <label>Fixture due time (Africa/Kampala)<input type="datetime-local" required value={due} onChange={(event) => setDue(event.target.value)} /></label>
      <label>Fixture expiry (Africa/Kampala)<input type="datetime-local" required value={expiry} onChange={(event) => setExpiry(event.target.value)} /></label>
      <p>Explicit test inputs only. No clinical week calculation or reminder offset is configured.</p>
      <button type="submit" disabled={blocked}>Plan held candidate</button>
    </form>}
    <h2>Occurrences and holds</h2>
    {!busy && rows.length === 0 && <p>No authorized scheduling records.</p>}
    <div className="content-list">{rows.map((row) => <article key={row.id}>
      <h3>{row.kind} — {row.state}</h3><p>Operational reference: {row.id}</p>
      <p>Fixture due: {displayKampala(row.due_at)} · Expires: {displayKampala(row.expires_at)}</p>
      <ul>{row.hold_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
      <button type="button" disabled={busy} onClick={() => open(row)}>History {row.id}</button>
      {can('message.plan') && !operations && <button type="button" disabled={blocked} onClick={() => write(
        `/message-occurrences/${row.id}/evaluate?facility_id=${encodeURIComponent(row.facility_id)}`, {}, row.version,
      )}>Recheck {row.id}</button>}
    </article>)}</div>
    {cursor && <button type="button" disabled={busy} onClick={async () => {
      setBusy(true); try { await load(cursor) } catch { setMessage('More scheduling records unavailable.') } finally { setBusy(false) }
    }}>Load more occurrences</button>}
    {selected && <article aria-label="Scheduling history"><h2>Scheduling history — {selected.id}</h2>
      <ol>{events.map((event) => <li key={event.sequence}>{event.sequence}. {event.event_type} — {event.state} · {displayKampala(event.created_at)}
        <ul>{event.hold_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul></li>)}</ol>
    </article>}
    {can('messaging.control') && <section aria-label="Synthetic messaging controls">
      <h2>{facility ? 'Facility' : 'Global'} synthetic controls</h2>
      <p>Pause: {control ? control.paused ? 'Paused' : 'Unpaused (all policy holds still apply)' : 'Unavailable'}</p>
      <ul aria-label="Synthetic budget metadata">{budgets.map((budget) => <li key={budget.id}>
        SYN cap {budget.cap} · Reserved {budget.reserved} · SYN per segment {budget.unit_cost} · Period ends {displayKampala(budget.period_end)}
      </li>)}</ul>
      <label>Synthetic control code<input value={code} onChange={(event) => setCode(event.target.value)} placeholder="SYN-CONTROL" /></label>
      <button type="button" disabled={blocked || !control || !/^SYN-[A-Z0-9-]{2,60}$/.test(code)} onClick={() => write(
        `/messaging/${scope}/pause`, { paused: !control!.paused, code, config: {} }, control!.version,
      )}>{control?.paused ? 'Unpause fixture scope' : 'Pause fixture scope'}</button>
      <form className="content-form" onSubmit={budget}>
        <h3>Immutable synthetic budget — SYN units only</h3>
        <label>Budget start (Africa/Kampala)<input required type="datetime-local" value={periodStart} onChange={(event) => setPeriodStart(event.target.value)} /></label>
        <label>Budget end (Africa/Kampala)<input required type="datetime-local" value={periodEnd} onChange={(event) => setPeriodEnd(event.target.value)} /></label>
        <label>Explicit synthetic cap<input required pattern="\d{1,10}(\.\d{1,4})?" value={cap} onChange={(event) => setCap(event.target.value)} /></label>
        <label>Explicit SYN cost per segment<input required pattern="\d{1,10}(\.\d{1,4})?" value={rate} onChange={(event) => setRate(event.target.value)} /></label>
        <button type="submit" disabled={blocked}>Create synthetic budget</button>
      </form>
      {facility && <form className="content-form" onSubmit={(event) => { event.preventDefault(); void write(
        `/messaging/facilities/${encodeURIComponent(facility)}/capacity-reservations`, { fixture_encoding: encoding, units: Number(units) },
      ) }}>
        <h3>Capacity simulation — no dispatch linkage</h3>
        <label>Encoding fixture<select value={encoding} onChange={(event) => setEncoding(event.target.value)}>
          <option value="gsm7">GSM-7 A</option><option value="gsm7_extension">GSM-7 extension ^</option><option value="unicode">Unicode surrogate pair</option>
        </select></label>
        <label>Synthetic repeated units<input required type="number" min={1} max={10000} value={units} onChange={(event) => setUnits(event.target.value)} /></label>
        <button type="submit" disabled={blocked}>Reserve synthetic capacity</button>
      </form>}
    </section>}
  </section>
}
