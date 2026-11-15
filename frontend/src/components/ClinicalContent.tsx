import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import type { StaffIdentity } from '../api/auth'
import { ApiError, apiFetch, apiFetchPage } from '../api/client'
import { displayKampala, kampalaToUtc } from '../api/appointmentTime'

interface Template {
  id: string; code: string; purpose: string; trigger_kind: string; gestational_week: number | null
  created_by: string; version: number
}
interface Version {
  id: string; template_id: string; template_code: string; author_id: string; language: string; revision: number
  state: string; version: number; template_version: number; text: string; content_digest: string
  effective_from: string | null; effective_to: string | null
  review: { reviewer_id: string; decision: string; content_digest: string; reason: string; clinical_signoff: false } | null
}
interface ContentEvent { id: string; sequence: number; event_type: string; actor_id: string; created_at: string; data: { digest: string; reason: string | null } }

export default function ClinicalContent({ identity }: { identity: StaffIdentity }) {
  const can = (permission: string) => identity.permissions.includes(`content.${permission}`)
  const [templates, setTemplates] = useState<Template[]>([])
  const [templateCursor, setTemplateCursor] = useState<string | null>(null)
  const [templateId, setTemplateId] = useState('')
  const [versions, setVersions] = useState<Version[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [filter, setFilter] = useState(can('review') ? 'submitted' : '')
  const [languageFilter, setLanguageFilter] = useState('')
  const [selected, setSelected] = useState<Version | null>(null)
  const [events, setEvents] = useState<ContentEvent[]>([])
  const [code, setCode] = useState('')
  const [purpose, setPurpose] = useState('weekly_advice')
  const [week, setWeek] = useState('')
  const [language, setLanguage] = useState('en')
  const [fixtureCode, setFixtureCode] = useState('')
  const [reason, setReason] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [busy, setBusy] = useState(true)
  const [loaded, setLoaded] = useState(false)
  const [needsRefresh, setNeedsRefresh] = useState(false)
  const [message, setMessage] = useState('')
  const retry = useRef<{ input: string; key: string } | null>(null)
  const savedVersionId = useRef<string | null>(null)
  const writeBlocked = busy || needsRefresh
  const parent = templates.find((row) => row.id === templateId)
  const placeholder = `SYNTHETIC PLACEHOLDER - NOT CLINICAL ADVICE - ${language} - SYN-${fixtureCode}`
  const versionPath = `/template-versions?limit=25${templateId ? `&template_id=${encodeURIComponent(templateId)}` : ''}${filter ? `&state=${filter}` : ''}${languageFilter ? `&language=${languageFilter}` : ''}`

  const loadTemplates = useCallback(async (next?: string, signal?: AbortSignal) => {
    const result = await apiFetchPage<Template>(`/message-templates${next ? `?cursor=${encodeURIComponent(next)}` : ''}`, { signal })
    if (!signal?.aborted) {
      setTemplates((previous) => [...new Map([...(next ? previous : previous.filter((row) => row.id === templateId)), ...result.data].map((row) => [row.id, row])).values()])
      setTemplateCursor(result.page.next_cursor)
    }
  }, [templateId])

  const loadVersions = useCallback(async (next?: string, signal?: AbortSignal) => {
    const result = await apiFetchPage<Version>(`${versionPath}${next ? `&cursor=${encodeURIComponent(next)}` : ''}`, { signal })
    if (!signal?.aborted) {
      setVersions((previous) => next ? [...new Map([...previous, ...result.data].map((row) => [row.id, row])).values()] : result.data)
      setCursor(result.page.next_cursor)
    }
  }, [versionPath])

  useEffect(() => {
    const controller = new AbortController()
    void Promise.resolve().then(async () => {
      setBusy(true); setLoaded(false); setSelected(null); setEvents([]); setMessage('')
      await Promise.all([loadTemplates(undefined, controller.signal), loadVersions(undefined, controller.signal)])
      if (!controller.signal.aborted) setLoaded(true)
    }).catch(() => { if (!controller.signal.aborted) setMessage('Content access is unavailable.') })
      .finally(() => { if (!controller.signal.aborted) setBusy(false) })
    return () => controller.abort()
  }, [loadTemplates, loadVersions])

  async function detail(id: string) {
    setBusy(true); setMessage(''); setSelected(null); setEvents([]); setReason(''); setFrom(''); setTo('')
    try {
      const row = await apiFetch<Version>(`/template-versions/${id}`)
      const [history, metadata] = await Promise.all([
        apiFetch<ContentEvent[]>(`/template-versions/${id}/events`),
        apiFetch<Template>(`/message-templates/${row.template_id}`),
      ])
      setSelected(row); setEvents(history)
      setTemplates((previous) => [metadata, ...previous.filter((template) => template.id !== metadata.id)])
      return true
    } catch { setMessage('This content version is unavailable.'); return false }
    finally { setBusy(false) }
  }

  async function refresh() {
    setBusy(true); setMessage('')
    try {
      await Promise.all([loadTemplates(), loadVersions()])
      const id = savedVersionId.current ?? selected?.id
      if (id && !await detail(id)) throw new Error('Content refresh unavailable')
      savedVersionId.current = null; setNeedsRefresh(false); setLoaded(true)
    } catch { setSelected(null); setEvents([]); setLoaded(false); setMessage('Content access is unavailable.') }
    finally { setBusy(false) }
  }

  async function write(path: string, body: unknown, version?: number, method: 'POST' | 'PATCH' = 'POST') {
    if (writeBlocked) return
    const input = JSON.stringify({ path, method, body })
    const key = retry.current?.input === input ? retry.current.key : crypto.randomUUID()
    retry.current = { input, key }
    setBusy(true); setMessage('')
    let result: Template | Version
    try {
      result = await apiFetch<Template | Version>(path, { method, body, idempotencyKey: key,
        ...(version !== undefined ? { ifMatch: `"${version}"` } : {}) })
    } catch (error) {
      if (error instanceof ApiError && [409, 412].includes(error.status)) {
        setMessage('Content changed or the publication interval conflicts. Refresh before retrying.')
      } else if (error instanceof ApiError && [401, 403, 404].includes(error.status)) {
        setSelected(null); setEvents([]); setMessage('This action is unauthorized or requires an independent reviewer.')
      } else setMessage('Content action could not be saved. Retry with the same details.')
      setBusy(false)
      return
    }
    // The mutation is confirmed. Read failures below must never turn it into
    // an uncertain write or offer the same completed operation with a new key.
    retry.current = null
    setReason(''); setFixtureCode(''); setFrom(''); setTo('')
    try {
      if (path === '/message-templates') {
        const created = result as Template
        setTemplates((previous) => [created, ...previous.filter((row) => row.id !== created.id)])
        setTemplateId(created.id); setCode(''); setWeek('')
      } else {
        const created = result as Version
        savedVersionId.current = created.id
        setSelected(created); setEvents([])
        setTemplates((previous) => previous.map((row) => row.id === created.template_id ? { ...row, version: created.template_version } : row))
        const [current, history, metadata] = await Promise.all([
          apiFetch<Version>(`/template-versions/${created.id}`),
          apiFetch<ContentEvent[]>(`/template-versions/${created.id}/events`),
          apiFetch<Template>(`/message-templates/${created.template_id}`),
        ])
        setSelected(current); setEvents(history)
        setTemplates((previous) => [metadata, ...previous.filter((row) => row.id !== metadata.id)])
        await loadVersions()
        savedVersionId.current = null
      }
      setMessage('Technical fixture action saved. D07 clinical sign-off and dispatch remain disabled.')
    } catch {
      setSelected(null); setEvents([]); setMessage(''); setNeedsRefresh(true)
    } finally { setBusy(false) }
  }

  async function createTemplate(event: FormEvent) {
    event.preventDefault()
    await write('/message-templates', { code, programme: 'antenatal', purpose,
      trigger_kind: purpose === 'weekly_advice' ? 'weekly' : 'appointment_reminder',
      ...(purpose === 'weekly_advice' ? { gestational_week: Number(week) } : {}) })
  }

  async function createDraft(event: FormEvent) {
    event.preventDefault()
    if (!parent) return
    await write('/template-versions', { template_id: parent.id, language, text: placeholder, allowed_parameters: [] }, parent.version)
  }

  async function publication(event: FormEvent) {
    event.preventDefault()
    if (!selected) return
    try { await write(`/template-versions/${selected.id}/publish`, { effective_from: kampalaToUtc(from),
      effective_to: to ? kampalaToUtc(to) : null }, selected.version) }
    catch { setMessage('Enter valid publication times in Africa/Kampala.') }
  }

  const validReason = /^SYN-[A-Z0-9][A-Z0-9-]{1,59}$/.test(reason)
  const validCode = /^[0-9A-F]{8,32}$/.test(fixtureCode)
  return <section className="content-width content-management" aria-labelledby="clinical-content-title">
    <h1 id="clinical-content-title">Clinical content — synthetic foundation</h1>
    <p role="note"><strong>D07 unresolved: technical fixture approval is NOT clinical sign-off.</strong> Only labelled
      placeholders are supported. Language slots are not approved translations. No medical advice, substitutions or SMS dispatch.</p>
    <button type="button" onClick={refresh} disabled={busy}>Refresh content</button>
    {message && <p role="status">{message}</p>}
    {needsRefresh && <p role="status">Fixture action saved, but the content view could not be refreshed. Use Refresh content to reload before another action.</p>}
    {busy && <p role="status">Loading content…</p>}
    {loaded && <>
      {can('author') && <form className="content-form" onSubmit={createTemplate}>
        <h2>Create synthetic template</h2>
        <label>Template fixture code<input required maxLength={64} value={code} onChange={(event) => setCode(event.target.value)} placeholder="SYN-TEMPLATE-EXAMPLE" /></label>
        <label>Template purpose<select value={purpose} onChange={(event) => setPurpose(event.target.value)}>
          <option value="weekly_advice">Weekly antenatal advice slot</option><option value="appointment_reminder">Appointment reminder slot</option>
        </select></label>
        {purpose === 'weekly_advice' && <label>Synthetic weekly index (metadata only)
          <input type="number" required min={0} max={2147483647} value={week} onChange={(event) => setWeek(event.target.value)} />
        </label>}
        <button type="submit" disabled={writeBlocked}>Create fixture template</button>
      </form>}
      <div className="content-filters">
        <label>Content template<select value={templateId} disabled={busy} onChange={(event) => setTemplateId(event.target.value)}>
          <option value="">All templates / review queue</option>{templates.map((row) => <option key={row.id} value={row.id}>{row.code}</option>)}
        </select></label>
        <label>Content state<select value={filter} disabled={busy} onChange={(event) => setFilter(event.target.value)}>
          <option value="">All authorized states</option>{['draft', 'submitted', 'approved', 'rejected', 'published', 'suspended', 'retired'].map((state) => <option key={state}>{state}</option>)}
        </select></label>
        <label>Language filter<select value={languageFilter} disabled={busy} onChange={(event) => setLanguageFilter(event.target.value)}>
          <option value="">Both authorized language slots</option><option value="en">English slot</option><option value="lg">Luganda slot</option>
        </select></label>
      </div>
      {templateCursor && <button type="button" disabled={busy} onClick={async () => { setBusy(true); try { await loadTemplates(templateCursor) } catch { setMessage('More templates are unavailable.') } finally { setBusy(false) } }}>Load more templates</button>}
      {can('author') && parent?.created_by === identity.user_id && <form className="content-form" onSubmit={createDraft}>
        <h2>Create a new version</h2>
        <label>Draft language slot<select value={language} onChange={(event) => setLanguage(event.target.value)}>
          <option value="en">English — fixture only</option><option value="lg">Luganda — fixture only</option>
        </select></label>
        <label>Placeholder hex identifier<input required maxLength={32} value={fixtureCode} onChange={(event) => setFixtureCode(event.target.value)} placeholder="A1B2C3D4" /></label>
        <output aria-label="Synthetic placeholder preview">{placeholder}</output>
        <p>No approved substitution parameters. Corrections to frozen content require a new version.</p>
        <button type="submit" disabled={writeBlocked || !validCode}>Save new fixture draft</button>
      </form>}
      <h2>Authorized version history / review queue</h2>
      <div className="content-list">{versions.map((row) => <article key={row.id}>
        <h3>{row.template_code} — {row.language} revision {row.revision}</h3><p>Technical state: {row.state}</p>
        <button type="button" disabled={busy} onClick={() => detail(row.id)}>Open {row.language} revision {row.revision}</button>
      </article>)}</div>
      {cursor && <button type="button" disabled={busy} onClick={async () => { setBusy(true); try { await loadVersions(cursor) } catch { setMessage('More versions are unavailable.') } finally { setBusy(false) } }}>Load more versions</button>}
      {selected && <article className="content-version" aria-label="Selected fixture version">
        <h2>{selected.template_code}: {selected.language} revision {selected.revision}</h2>
        <p>Technical state: {selected.state} · ETag version: {selected.version}</p>
        <p>{selected.text}</p><p>Exact SHA-256 digest: <code>{selected.content_digest}</code></p>
        <p>Dispatch held: D07_unconfigured · Clinical sign-off: false</p>
        {selected.review && <p>Independent technical fixture review: {selected.review.decision}, reviewer {selected.review.reviewer_id}, code {selected.review.reason}</p>}
        {selected.effective_from && <p>Controlled publication interval: {displayKampala(selected.effective_from)} to {selected.effective_to ? displayKampala(selected.effective_to) : 'open-ended'}</p>}
        {selected.state === 'draft' && selected.author_id === identity.user_id && <>
          {can('author') && <label>Replacement draft hex identifier<input value={fixtureCode} maxLength={32} onChange={(event) => setFixtureCode(event.target.value)} /></label>}
          {can('author') && <button type="button" disabled={writeBlocked || !validCode} onClick={() => write(`/template-versions/${selected.id}`, {
            text: `SYNTHETIC PLACEHOLDER - NOT CLINICAL ADVICE - ${selected.language} - SYN-${fixtureCode}`, allowed_parameters: [],
          }, selected.version, 'PATCH')}>Save draft edit</button>}
          {can('submit') && <button type="button" disabled={writeBlocked} onClick={() => write(`/template-versions/${selected.id}/submit`, {}, selected.version)}>Submit fixture for independent review</button>}
        </>}
        {selected.author_id === identity.user_id && selected.state === 'submitted' && <p>You cannot review your own content, even with a reviewer role.</p>}
        {(['submitted', 'published', 'suspended'].includes(selected.state)) && <label>Synthetic review/action reason code
          <input value={reason} maxLength={64} onChange={(event) => setReason(event.target.value)} placeholder="SYN-FIXTURE-REVIEW" />
        </label>}
        {can('review') && selected.state === 'submitted' && selected.author_id !== identity.user_id && <>
          <button type="button" disabled={writeBlocked || !validReason} onClick={() => write(`/template-versions/${selected.id}/review`, { decision: 'approve', reason, content_digest: selected.content_digest }, selected.version)}>Approve exact fixture digest</button>
          <button type="button" disabled={writeBlocked || !validReason} onClick={() => write(`/template-versions/${selected.id}/review`, { decision: 'reject', reason, content_digest: selected.content_digest }, selected.version)}>Reject exact fixture digest</button>
        </>}
        {can('publish') && selected.state === 'approved' && <form className="content-form" onSubmit={publication}>
          <label>Publication start (Africa/Kampala)<input type="datetime-local" required value={from} onChange={(event) => setFrom(event.target.value)} /></label>
          <label>Publication end (Africa/Kampala, optional)<input type="datetime-local" value={to} onChange={(event) => setTo(event.target.value)} /></label>
          <button type="submit" disabled={writeBlocked}>Publish approved fixture period</button>
        </form>}
        {can('publish') && selected.state === 'suspended' && <button type="button" disabled={writeBlocked} onClick={() => write(`/template-versions/${selected.id}/publish`, {
          effective_from: selected.effective_from, effective_to: selected.effective_to,
        }, selected.version)}>Resume original fixture publication</button>}
        {can('suspend') && selected.state === 'published' && <button type="button" disabled={writeBlocked || !validReason} onClick={() => write(`/template-versions/${selected.id}/suspend`, { reason }, selected.version)}>Suspend fixture immediately</button>}
        {can('suspend') && ['published', 'suspended'].includes(selected.state) && <button type="button" disabled={writeBlocked || !validReason} onClick={() => write(`/template-versions/${selected.id}/retire`, { reason }, selected.version)}>Retire fixture permanently</button>}
        <h3>Immutable action history</h3><ol>{events.map((event) => <li key={event.id}>{event.sequence}. {event.event_type} — {displayKampala(event.created_at)}
          <p>Actor: {event.actor_id}</p><code>{event.data.digest}</code>{event.data.reason && <p>{event.data.reason}</p>}
        </li>)}</ol>
      </article>}
    </>}
  </section>
}
