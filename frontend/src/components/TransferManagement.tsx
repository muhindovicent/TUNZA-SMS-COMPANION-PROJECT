import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import type { StaffIdentity } from '../api/auth'
import { ApiError, apiFetch, apiFetchPage } from '../api/client'

interface Transfer {
  id: string; source_facility_id: string; destination_facility_id: string
  state: 'pending' | 'accepted' | 'rejected' | 'cancelled'; version: number; synthetic: boolean
  decision?: { outcome: string; reason_code: string; destination_reference?: string }
}
interface Capabilities { synthetic_enabled: boolean; live_enabled: false; unresolved_decisions: string[] }
interface Option { id: string; name?: string; code?: string; display_name?: string }

export default function TransferManagement({ identity, facilityId, episode, onChanged }: {
  identity: StaffIdentity
  facilityId?: string
  episode?: { id: string; version: number; assignment_id: string | null; lifecycle: string }
  onChanged?: () => Promise<void>
}) {
  const [selectedFacility, setSelectedFacility] = useState(facilityId ?? identity.facilities[0]?.id ?? '')
  const facility = facilityId ?? selectedFacility
  const [inboxDirection, setInboxDirection] = useState('incoming')
  const incoming = !episode && inboxDirection === 'incoming'
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null)
  const [rows, setRows] = useState<Transfer[]>([])
  const [destinations, setDestinations] = useState<Option[]>([])
  const [destination, setDestination] = useState('')
  const [fixture, setFixture] = useState('')
  const [filter, setFilter] = useState(incoming ? 'pending' : 'all')
  const [cursor, setCursor] = useState<string | null>(null)
  const [busy, setBusy] = useState(true)
  const [loaded, setLoaded] = useState(false)
  const [message, setMessage] = useState('')
  const [action, setAction] = useState<{ row: Transfer; kind: 'accept' | 'reject' | 'cancel' } | null>(null)
  const [reason, setReason] = useState('')
  const [receivers, setReceivers] = useState<Option[]>([])
  const [receiver, setReceiver] = useState('')
  const [language, setLanguage] = useState('')
  const [handover, setHandover] = useState<{ id: string; fixture_code: string } | null>(null)
  const [receipt, setReceipt] = useState<Transfer | null>(null)
  const retry = useRef<{ input: string; key: string } | null>(null)
  const scope = `facility_id=${encodeURIComponent(facility)}`
  const canInitiate = identity.permissions.includes('transfer.initiate')
  const canAccept = identity.permissions.includes('transfer.accept')
  const canCancel = identity.permissions.includes('transfer.cancel')
  const direction = incoming ? 'incoming' : 'outgoing'
  const path = `/transfers?${scope}&direction=${direction}&state=${filter}${episode ? `&episode_id=${encodeURIComponent(episode.id)}` : ''}`

  const load = useCallback(async (next?: string, signal?: AbortSignal) => {
    const mode = await apiFetch<Capabilities>('/transfers/capabilities', { signal })
    if (signal?.aborted) return
    setCapabilities(mode)
    if (!mode.synthetic_enabled || !facility) return
    const result = await apiFetchPage<Transfer>(`${path}${next ? `&cursor=${encodeURIComponent(next)}` : ''}`, { signal })
    if (signal?.aborted) return
    setRows((previous) => next ? [...new Map([...previous, ...result.data].map((row) => [row.id, row])).values()] : result.data)
    setCursor(result.page.next_cursor)
    if (!incoming && canInitiate) {
      const choices = await apiFetch<Option[]>(`/transfers/destinations?${scope}`, { signal })
      if (!signal?.aborted) setDestinations(choices)
    }
    if (!signal?.aborted) setLoaded(true)
  }, [facility, path, incoming, canInitiate, scope])

  useEffect(() => {
    const controller = new AbortController()
    void Promise.resolve().then(async () => {
      setBusy(true); setLoaded(false); setRows([]); setAction(null); setHandover(null); setReceipt(null); setMessage('')
      await load(undefined, controller.signal)
    }).catch(() => { if (!controller.signal.aborted) setMessage('Transfer records are unavailable.') })
      .finally(() => { if (!controller.signal.aborted) setBusy(false) })
    return () => controller.abort()
  }, [load])

  async function refresh(next?: string) {
    setBusy(true); setMessage(''); setAction(null); setHandover(null)
    try { await load(next); await onChanged?.().catch(() => undefined) }
    catch { setRows([]); setLoaded(false); setMessage('Transfer records are unavailable.') }
    finally { setBusy(false) }
  }

  async function choose(row: Transfer, kind: 'accept' | 'reject' | 'cancel') {
    setAction({ row, kind }); setReason(''); setReceiver(''); setLanguage(''); setReceivers([])
    if (kind !== 'accept') return
    setBusy(true)
    try { setReceivers(await apiFetch<Option[]>(`/transfers/${row.id}/receivers?${scope}`)) }
    catch { setAction(null); setMessage('Authorized destination care staff are unavailable.') }
    finally { setBusy(false) }
  }

  async function showHandover(row: Transfer) {
    setBusy(true); setHandover(null)
    try {
      const data = await apiFetch<{ synthetic: true; fixture_code: string }>(`/transfers/${row.id}/handover?${scope}`)
      setHandover({ id: row.id, fixture_code: data.fixture_code })
    } catch { setMessage('Synthetic handover is unavailable.') }
    finally { setBusy(false) }
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!capabilities?.synthetic_enabled) return
    const target = action ? `/transfers/${action.row.id}/${action.kind === 'cancel' ? 'cancel' : 'decision'}` : '/transfers'
    const body = action ? { reason, ...(action.kind === 'cancel' ? {} : { decision: action.kind }),
      ...(action.kind === 'accept' ? { receiver_membership_id: receiver, synthetic_language: language } : {}) }
      : { source_assignment_id: episode?.assignment_id, destination_facility_id: destination,
        handover: { synthetic: true, fixture_code: fixture } }
    const input = JSON.stringify({ target, body, facility })
    const key = retry.current?.input === input ? retry.current.key : crypto.randomUUID()
    retry.current = { input, key }
    setBusy(true); setMessage('')
    try {
      const result = await apiFetch<Transfer>(`${target}?${scope}`, { method: 'POST', body,
        idempotencyKey: key, ifMatch: `"${action?.row.version ?? episode?.version}"` })
      retry.current = null
      setReceipt(result)
      setRows((previous) => [result, ...previous.filter((row) => row.id !== result.id)])
      setAction(null); setFixture(''); setDestination(''); setHandover(null)
      // Acceptance can intentionally revoke source clinical access. Its receipt
      // stays bounded; do not fetch a full destination chart from this panel.
      if (action && incoming) await load()
      else await onChanged?.()
      setMessage('Synthetic transfer saved. D04/D09 remain unresolved; live transfers and messaging stay disabled.')
    } catch (error) {
      if (error instanceof ApiError && [409, 412].includes(error.status)) {
        setMessage('The transfer changed or conflicts. Refresh before retrying.')
      } else if (error instanceof ApiError && [401, 403, 404].includes(error.status)) {
        setRows([]); setLoaded(false); setAction(null); setMessage('Transfer access is unavailable or the fixture workflow is disabled.')
      } else setMessage('Transfer could not be saved. Retry with the same details.')
    } finally { setBusy(false) }
  }

  return <section className="transfer-panel" aria-labelledby={!episode ? 'transfer-inbox-title' : 'transfer-source-title'}>
    <h3 id={!episode ? 'transfer-inbox-title' : 'transfer-source-title'}>{!episode ? 'Synthetic transfer inbox' : 'Controlled facility transfers'}</h3>
    <p>D04/D09 remain unresolved. This is a fixture-only demonstration, not approved live transfer
      authority. No clinical handover, consent portability or messaging activation is enabled.</p>
    {!facilityId && <label>Transfer facility
      <select value={selectedFacility} disabled={busy} onChange={(event) => setSelectedFacility(event.target.value)}>
        {identity.facilities.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select>
    </label>}
    {capabilities && !capabilities.synthetic_enabled && <p role="status">Synthetic transfers are disabled. D04/D09 approvals remain pending.</p>}
    <button type="button" disabled={busy} onClick={() => refresh()}>Refresh transfers</button>
    {busy && <p role="status">Loading transfer records…</p>}
    {message && <p role="status">{message}</p>}
    {incoming && receipt?.decision?.destination_reference && <p role="status">
      Accepted synthetic destination reference: {receipt.decision.destination_reference}
    </p>}
    {capabilities?.synthetic_enabled && loaded && <>
      {!episode && <label>Transfer direction
        <select value={inboxDirection} disabled={busy} onChange={(event) => setInboxDirection(event.target.value)}>
          <option value="incoming" disabled={!canAccept}>Incoming decisions</option>
          <option value="outgoing" disabled={!canCancel}>Outgoing cancellation receipts</option>
        </select>
      </label>}
      <label>Transfer status
        <select value={filter} disabled={busy} onChange={(event) => setFilter(event.target.value)}>
          {['pending', 'accepted', 'rejected', 'cancelled', 'all'].map((state) => <option key={state}>{state}</option>)}
        </select>
      </label>
      {!incoming && canInitiate && episode?.lifecycle === 'ongoing' && episode.assignment_id &&
        !rows.some((row) => row.state === 'pending') && !action && <form className="transfer-form" onSubmit={submit}>
          <h4>Request synthetic transfer</h4>
          <label>Synthetic destination
            <select required value={destination} onChange={(event) => setDestination(event.target.value)}>
              <option value="">Select a synthetic facility</option>
              {destinations.map((item) => <option key={item.id} value={item.id}>{item.code} — {item.name}</option>)}
            </select>
          </label>
          <label>Synthetic handover fixture code
            <input required value={fixture} onChange={(event) => setFixture(event.target.value)} pattern="SYN-[A-Z0-9][A-Z0-9-]{1,59}" maxLength={64} placeholder="SYN-HANDOVER-EXAMPLE" />
          </label>
          <button type="submit" disabled={busy}>Request synthetic transfer</button>
        </form>}
      {action && <form className="transfer-form" onSubmit={submit}>
        <h4>{action.kind} synthetic request {action.row.id}</h4>
        <label>Synthetic decision reason code
          <input value={reason} onChange={(event) => setReason(event.target.value)} required maxLength={64}
            pattern="SYN-[A-Z0-9][A-Z0-9-]{1,59}" placeholder="SYN-DECISION-EXAMPLE" />
        </label>
        {action.kind === 'accept' && <>
          <label>Existing destination HCW
            <select required value={receiver} onChange={(event) => setReceiver(event.target.value)}>
              <option value="">Select explicitly assigned care staff</option>
              {receivers.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}
            </select>
          </label>
          <label>Synthetic destination language
            <select required value={language} onChange={(event) => setLanguage(event.target.value)}>
              <option value="">Select fixture language</option><option value="en">English</option><option value="lg">Luganda</option>
            </select>
          </label>
        </>}
        <button type="submit" disabled={busy}>Save synthetic transfer decision</button>
        <button type="button" disabled={busy} onClick={() => setAction(null)}>Close decision</button>
      </form>}
      <div className="transfer-list">{rows.map((row) => <article key={row.id} aria-label={`Transfer ${row.id}`}>
        <h4>Transfer {row.id}</h4><p>State: {row.state} · Version: {row.version}</p>
        <p>Source facility: {row.source_facility_id}</p><p>Destination facility: {row.destination_facility_id}</p>
        {row.decision?.destination_reference && <p>Destination synthetic reference: {row.decision.destination_reference}</p>}
        {row.decision?.reason_code && <p>Decision code: {row.decision.reason_code}</p>}
        {['pending', 'accepted'].includes(row.state) && <button type="button" disabled={busy} onClick={() => showHandover(row)}>View synthetic handover</button>}
        {row.state === 'pending' && incoming && canAccept && <>
          <button type="button" disabled={busy} onClick={() => choose(row, 'accept')}>Accept synthetic transfer</button>
          <button type="button" disabled={busy} onClick={() => choose(row, 'reject')}>Reject synthetic transfer</button>
        </>}
        {row.state === 'pending' && !incoming && canCancel && <button type="button" disabled={busy} onClick={() => choose(row, 'cancel')}>Cancel synthetic transfer</button>}
      </article>)}</div>
      {rows.length === 0 && <p>No transfers in this selection.</p>}
      {cursor && <button type="button" disabled={busy} onClick={() => refresh(cursor)}>Load more transfers</button>}
      {handover && <p role="status">Fixture handover for {handover.id}: {handover.fixture_code}. No clinical fields are configured.</p>}
    </>}
  </section>
}
