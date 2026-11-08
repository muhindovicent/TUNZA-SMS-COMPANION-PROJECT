import { describe, expect, it, vi } from 'vitest'
import { loadFoundationStatus } from './health'

function healthyResponse(url: string) {
  return Response.json({
    data: url.endsWith('/live')
      ? { service: 'tunza-api', status: 'ok' }
      : { service: 'tunza-api', status: 'ready', checks: { database: 'connected' } },
  })
}

describe('foundation API client', () => {
  it('checks API and database independently with the documented envelope', async () => {
    const fetchMock = vi.fn(async (url: string) => healthyResponse(url))
    vi.stubGlobal('fetch', fetchMock)
    expect(await loadFoundationStatus()).toEqual({ api: 'reachable', database: 'connected' })
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/health/live', expect.objectContaining({ cache: 'no-store' }))
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/health/ready', expect.any(Object))
  })

  it('supports a configured API base URL without duplicate slashes', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://synthetic.example.test/api/v1/')
    const fetchMock = vi.fn(async (url: string) => healthyResponse(url))
    vi.stubGlobal('fetch', fetchMock)
    await loadFoundationStatus()
    expect(fetchMock).toHaveBeenCalledWith('https://synthetic.example.test/api/v1/health/live', expect.any(Object))
  })

  it('does not treat a database outage as an API outage', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => url.endsWith('/live')
      ? healthyResponse(url)
      : Response.json({ data: { status: 'not_ready' } }, { status: 503 })))
    expect(await loadFoundationStatus()).toEqual({ api: 'reachable', database: 'unavailable' })
  })

  it.each([
    { data: { service: 'another-service', status: 'ok' } },
    { status: 'ok' },
    null,
    { data: { service: 'tunza-api', status: 'ready', checks: { database: 'unavailable' } } },
  ])('rejects malformed or misleading health responses: %j', async (body) => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(body)))
    expect(await loadFoundationStatus()).toEqual({ api: 'unavailable', database: 'unavailable' })
  })

  it('handles non-JSON development proxy errors', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>proxy error</html>')))
    expect(await loadFoundationStatus()).toEqual({ api: 'unavailable', database: 'unavailable' })
  })

  it('handles a network failure without exposing the exception', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('synthetic-internal-error')))
    expect(await loadFoundationStatus()).toEqual({ api: 'unavailable', database: 'unavailable' })
  })

  it('passes cancellation and a finite timeout signal to fetch', async () => {
    const controller = new AbortController()
    controller.abort()
    const fetchMock = vi.fn(async (_url: string, options: RequestInit) => {
      expect(options.signal?.aborted).toBe(true)
      throw new DOMException('Aborted', 'AbortError')
    })
    vi.stubGlobal('fetch', fetchMock)
    expect(await loadFoundationStatus(controller.signal)).toEqual({ api: 'unavailable', database: 'unavailable' })
  })
})
