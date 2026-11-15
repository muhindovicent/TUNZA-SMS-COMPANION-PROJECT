import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { StaffIdentity } from '../api/auth'
import { ApiError, apiFetch, apiFetchPage } from '../api/client'
import { loadFoundationStatus } from '../api/health'
import ClinicalContent from './ClinicalContent'
import StaffShell from './StaffShell'

vi.mock('../api/client', async (original) => ({ ...await original<typeof import('../api/client')>(), apiFetch: vi.fn(), apiFetchPage: vi.fn() }))
vi.mock('../api/health', () => ({ loadFoundationStatus: vi.fn().mockResolvedValue({ api: 'reachable', database: 'connected' }) }))

const author: StaffIdentity = { user_id: 'author', display_name: 'Synthetic Author', state: 'active', facilities: [], bindings: [],
  permissions: ['content.read', 'content.author', 'content.submit'] }
const reviewer: StaffIdentity = { ...author, user_id: 'reviewer', display_name: 'Synthetic Reviewer',
  permissions: ['content.read', 'content.review', 'content.publish', 'content.suspend'] }
const text = 'SYNTHETIC PLACEHOLDER - NOT CLINICAL ADVICE - en - SYN-A1B2C3D4'
const initialTemplate = { id: 'template-a', code: 'SYN-WEEKLY-FIXTURE', purpose: 'weekly_advice', trigger_kind: 'weekly', gestational_week: 1, created_by: 'author', version: 1 }
const initialVersion = { id: 'version-a', template_id: 'template-a', template_code: initialTemplate.code, author_id: 'author', language: 'en', revision: 1,
  text, content_digest: 'a'.repeat(64), state: 'submitted', version: 2, template_version: 2,
  effective_from: null as string | null, effective_to: null as string | null,
  review: null as { reviewer_id: string; decision: string; reason: string; content_digest: string; clinical_signoff: false } | null }
let template: typeof initialTemplate
let version: typeof initialVersion
let events: { id: string; sequence: number; event_type: string; actor_id: string; created_at: string; data: { digest: string; reason: string | null } }[]

beforeEach(() => {
  vi.resetAllMocks(); template = { ...initialTemplate }; version = { ...initialVersion }
  vi.mocked(loadFoundationStatus).mockResolvedValue({ api: 'reachable', database: 'connected' })
  events = [{ id: 'event-1', sequence: 1, event_type: 'draft_created', actor_id: 'author', created_at: '2026-11-15T06:00:00Z', data: { digest: version.content_digest, reason: null } }]
  vi.mocked(apiFetchPage).mockImplementation(async (path) => {
    if (path.startsWith('/message-templates')) return { data: [{ ...template }], page: { next_cursor: null } }
    const filter = new URL(path, 'https://synthetic.test').searchParams
    const visible = (!filter.get('state') || filter.get('state') === version.state) && (!filter.get('template_id') || filter.get('template_id') === template.id)
    return { data: visible ? [{ ...version }] : [], page: { next_cursor: null } }
  })
  vi.mocked(apiFetch).mockImplementation(async (path, request) => {
    if (request?.method === 'POST' && path === '/message-templates') {
      template = { ...template, ...(request.body as object), id: 'new-template', version: 1 }
      return { ...template }
    }
    if (request?.method === 'POST' && path === '/template-versions') {
      version = { ...version, ...(request.body as object), id: 'new-version', template_id: template.id, template_code: template.code, state: 'draft', version: 1, review: null }
      return { ...version }
    }
    if (request?.method === 'PATCH') { version = { ...version, ...(request.body as object), version: version.version + 1 }; return { ...version } }
    if (request?.method === 'POST') {
      const action = path.split('/').at(-1)
      const body = request.body as { decision?: string; reason?: string; effective_from?: string; effective_to?: string | null }
      const state = action === 'submit' ? 'submitted' : action === 'review' ? body.decision === 'approve' ? 'approved' : 'rejected'
        : action === 'publish' ? 'published' : action === 'suspend' ? 'suspended' : 'retired'
      version = { ...version, state, version: version.version + 1 }
      if (action === 'review') version.review = { reviewer_id: 'reviewer', decision: body.decision!, reason: body.reason!, content_digest: version.content_digest, clinical_signoff: false }
      if (action === 'publish') version = { ...version, effective_from: body.effective_from!, effective_to: body.effective_to ?? null }
      template.version += 1
      events = [...events, { id: `event-${events.length + 1}`, sequence: version.version, event_type: state, actor_id: 'reviewer', created_at: '2026-11-15T07:00:00Z', data: { digest: version.content_digest, reason: body.reason ?? null } }]
      return { ...version }
    }
    if (path.includes('/events')) return structuredClone(events)
    if (path.startsWith('/message-templates/')) return { ...template }
    return { ...version }
  })
})

async function open(identity = reviewer) {
  render(<ClinicalContent identity={identity} />)
  await screen.findByLabelText('Content state')
  if (identity.permissions.includes('content.review') && version.state !== 'submitted') {
    fireEvent.change(screen.getByLabelText('Content state'), { target: { value: version.state } })
  }
  fireEvent.click(await screen.findByRole('button', { name: 'Open en revision 1' }))
  await screen.findByRole('article', { name: 'Selected fixture version' })
  await waitFor(() => expect(screen.queryByText('Loading content…')).toBeNull())
}

describe('synthetic clinical content foundation', () => {
  it('uses permission-based navigation without opening patient interfaces', async () => {
    const view = render(<StaffShell identity={reviewer} onSignedOut={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Clinical content' }))
    await screen.findByRole('heading', { name: 'Clinical content — synthetic foundation' })
    expect(screen.getByText(/technical fixture approval is NOT clinical sign-off/)).toBeInTheDocument()
    expect(vi.mocked(apiFetchPage).mock.calls.every(([path]) => !path.includes('facility_id'))).toBe(true)
    view.unmount()
    render(<StaffShell identity={{ ...reviewer, permissions: ['patient.read'] }} onSignedOut={vi.fn()} />)
    expect(screen.queryByRole('button', { name: 'Clinical content' })).toBeNull()
  })

  it('creates synthetic metadata and a separate Luganda placeholder version, with no parameters or translation', async () => {
    render(<ClinicalContent identity={author} />)
    await screen.findByRole('button', { name: 'Create fixture template' })
    fireEvent.change(screen.getByLabelText('Template fixture code'), { target: { value: 'SYN-REMINDER-FIXTURE' } })
    fireEvent.change(screen.getByLabelText('Template purpose'), { target: { value: 'appointment_reminder' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create fixture template' }))
    await screen.findByRole('button', { name: 'Save new fixture draft' })
    await waitFor(() => expect(screen.queryByText('Loading content…')).toBeNull())
    fireEvent.change(screen.getByLabelText('Draft language slot'), { target: { value: 'lg' } })
    fireEvent.change(screen.getByLabelText('Placeholder hex identifier'), { target: { value: 'DEADBEEF' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save new fixture draft' }))
    await screen.findByRole('article', { name: 'Selected fixture version' })
    expect(apiFetch).toHaveBeenCalledWith('/template-versions', expect.objectContaining({ ifMatch: '"1"', idempotencyKey: expect.any(String), body: {
      template_id: 'new-template', language: 'lg', text: 'SYNTHETIC PLACEHOLDER - NOT CLINICAL ADVICE - lg - SYN-DEADBEEF', allowed_parameters: [],
    } }))
  })

  it('submits owned drafts and removes editing controls after submission', async () => {
    version = { ...version, state: 'draft', version: 1 }
    await open(author)
    fireEvent.click(screen.getByRole('button', { name: 'Submit fixture for independent review' }))
    await screen.findByText('You cannot review your own content, even with a reviewer role.')
    expect(apiFetch).toHaveBeenCalledWith('/template-versions/version-a/submit', expect.objectContaining({ ifMatch: '"1"', body: {} }))
    expect(screen.queryByRole('button', { name: 'Save draft edit' })).toBeNull()
    expect(screen.getByText('Dispatch held: D07_unconfigured · Clinical sign-off: false')).toBeInTheDocument()
  })

  it('requires an exact version digest in independent technical review', async () => {
    await open()
    fireEvent.change(screen.getByLabelText('Synthetic review/action reason code'), { target: { value: 'SYN-FIXTURE-REVIEW' } })
    fireEvent.click(screen.getByRole('button', { name: 'Approve exact fixture digest' }))
    await screen.findByRole('button', { name: 'Publish approved fixture period' })
    expect(apiFetch).toHaveBeenCalledWith('/template-versions/version-a/review', expect.objectContaining({ ifMatch: '"2"', body: {
      decision: 'approve', reason: 'SYN-FIXTURE-REVIEW', content_digest: 'a'.repeat(64),
    } }))
  })

  it('does not offer review of own submitted content even with both roles', async () => {
    await open({ ...author, permissions: [...author.permissions, ...reviewer.permissions] })
    expect(screen.queryByRole('button', { name: 'Approve exact fixture digest' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Reject exact fixture digest' })).toBeNull()
  })

  it('converts explicit publication periods to UTC and exposes no real clinical sign-off', async () => {
    version = { ...version, state: 'approved', version: 3 }
    await open()
    fireEvent.change(screen.getByLabelText('Publication start (Africa/Kampala)'), { target: { value: '2027-01-01T09:00' } })
    fireEvent.change(screen.getByLabelText('Publication end (Africa/Kampala, optional)'), { target: { value: '2027-02-01T09:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Publish approved fixture period' }))
    await screen.findByRole('button', { name: 'Suspend fixture immediately' })
    expect(apiFetch).toHaveBeenCalledWith('/template-versions/version-a/publish', expect.objectContaining({ ifMatch: '"3"', body: {
      effective_from: '2027-01-01T06:00:00.000Z', effective_to: '2027-02-01T06:00:00.000Z',
    } }))
    expect(screen.getByText('Dispatch held: D07_unconfigured · Clinical sign-off: false')).toBeInTheDocument()
  })

  it('suspends a publication and resumes only its original interval', async () => {
    version = { ...version, state: 'published', version: 4, effective_from: '2020-01-01T00:00:00Z', effective_to: '2030-01-01T00:00:00Z' }
    render(<ClinicalContent identity={reviewer} />)
    await screen.findByLabelText('Content state')
    fireEvent.change(screen.getByLabelText('Content state'), { target: { value: '' } })
    fireEvent.click(await screen.findByRole('button', { name: 'Open en revision 1' }))
    await screen.findByRole('article', { name: 'Selected fixture version' })
    fireEvent.change(screen.getByLabelText('Synthetic review/action reason code'), { target: { value: 'SYN-SUSPEND' } })
    fireEvent.click(screen.getByRole('button', { name: 'Suspend fixture immediately' }))
    await screen.findByRole('button', { name: 'Resume original fixture publication' })
    fireEvent.click(screen.getByRole('button', { name: 'Resume original fixture publication' }))
    await screen.findByRole('button', { name: 'Suspend fixture immediately' })
    expect(apiFetch).toHaveBeenCalledWith('/template-versions/version-a/publish', expect.objectContaining({ body: {
      effective_from: '2020-01-01T00:00:00Z', effective_to: '2030-01-01T00:00:00Z',
    } }))
  })

  it('preserves retry keys for uncertain technical review requests', async () => {
    const implementation = vi.mocked(apiFetch).getMockImplementation()!
    let failed = false
    vi.mocked(apiFetch).mockImplementation(async (path, request) => {
      if (path.endsWith('/review') && !failed) { failed = true; throw new ApiError('unavailable', 'Synthetic audit failure', 503) }
      return implementation(path, request)
    })
    await open()
    fireEvent.change(screen.getByLabelText('Synthetic review/action reason code'), { target: { value: 'SYN-REVIEW' } })
    fireEvent.click(screen.getByRole('button', { name: 'Reject exact fixture digest' }))
    await screen.findByText('Content action could not be saved. Retry with the same details.')
    fireEvent.click(screen.getByRole('button', { name: 'Reject exact fixture digest' }))
    await screen.findByText('Technical fixture action saved. D07 clinical sign-off and dispatch remain disabled.')
    const writes = vi.mocked(apiFetch).mock.calls.filter(([path]) => path.endsWith('/review'))
    expect(writes[0][1]?.idempotencyKey).toBe(writes[1][1]?.idempotencyKey)
  })

  it('does not disclose content on authorization failure', async () => {
    vi.mocked(apiFetchPage).mockRejectedValue(new ApiError('forbidden', 'Unavailable', 403))
    render(<ClinicalContent identity={reviewer} />)
    await screen.findByText('Content access is unavailable.')
    expect(screen.queryByRole('article', { name: 'Selected fixture version' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Approve exact fixture digest' })).toBeNull()
  })
})
