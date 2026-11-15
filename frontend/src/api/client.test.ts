import { afterEach, expect, it, vi } from 'vitest'
import { apiFetch } from './client'

afterEach(() => vi.unstubAllGlobals())

it('sends dating version, idempotency and CSRF together with session credentials', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ data: { csrf_token: 'synthetic-csrf' } })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ data: { id: 'synthetic-revision' } }), { status: 201 }))
  vi.stubGlobal('fetch', fetchMock)
  await apiFetch('/pregnancy-episodes/synthetic/dating-revisions?facility_id=synthetic', {
    method: 'POST', body: { basis: 'synthetic', estimated_due_date: '2027-06-15', reason: 'Synthetic test' },
    idempotencyKey: 'synthetic-key', ifMatch: '"2"',
  })
  expect(fetchMock.mock.calls[1][1]).toMatchObject({
    credentials: 'same-origin', cache: 'no-store', method: 'POST',
    headers: { 'Idempotency-Key': 'synthetic-key', 'If-Match': '"2"',
      'X-CSRF-Token': 'synthetic-csrf', 'Content-Type': 'application/json' },
  })
})
