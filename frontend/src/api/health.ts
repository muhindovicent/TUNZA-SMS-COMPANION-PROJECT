export interface FoundationStatus {
  api: 'reachable' | 'unavailable'
  database: 'connected' | 'unavailable'
}

type Probe = 'live' | 'ready'

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

async function probe(kind: Probe, signal?: AbortSignal): Promise<boolean> {
  const baseUrl = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/+$/, '')
  const timeout = AbortSignal.timeout(5000)

  try {
    const response = await fetch(`${baseUrl}/health/${kind}`, {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
      signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
    })
    if (!response.ok) return false

    const body: unknown = await response.json()
    if (!isObject(body) || !isObject(body.data) || body.data.service !== 'tunza-api') {
      return false
    }
    if (kind === 'live') return body.data.status === 'ok'
    return (
      body.data.status === 'ready' &&
      isObject(body.data.checks) &&
      body.data.checks.database === 'connected'
    )
  } catch {
    // Network errors, timeouts and non-JSON proxy responses are all unavailable,
    // never raw exception text rendered to the browser.
    return false
  }
}

export async function loadFoundationStatus(signal?: AbortSignal): Promise<FoundationStatus> {
  const [api, database] = await Promise.all([probe('live', signal), probe('ready', signal)])
  return {
    api: api ? 'reachable' : 'unavailable',
    database: database ? 'connected' : 'unavailable',
  }
}
