export class ApiError extends Error {
  readonly code: string
  readonly status: number

  constructor(code: string, message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
  }
}

const MUTATING_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

export interface ApiRequest {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  signal?: AbortSignal
  idempotencyKey?: string
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function apiBaseUrl(): string {
  return (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/+$/, '')
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json()
  } catch {
    return null
  }
}

export async function fetchCsrfToken(signal?: AbortSignal): Promise<string> {
  let response: Response
  try {
    response = await fetch(`${apiBaseUrl()}/auth/csrf`, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      credentials: 'same-origin',
      cache: 'no-store',
      signal,
    })
  } catch {
    throw new ApiError(
      'network_error',
      'Unable to reach the server. Check your connection and try again.',
      0,
    )
  }
  const body = await readJson(response)
  if (
    !response.ok ||
    !isObject(body) ||
    !isObject(body.data) ||
    typeof body.data.csrf_token !== 'string'
  ) {
    throw new ApiError(
      'csrf_failed',
      'Your session could not be verified. Refresh the page and try again.',
      response.status,
    )
  }
  return body.data.csrf_token
}

export async function apiFetch<T>(path: string, request: ApiRequest = {}): Promise<T> {
  const method = request.method ?? 'GET'
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (request.idempotencyKey) headers['Idempotency-Key'] = request.idempotencyKey
  let payload: string | undefined
  if (request.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(request.body)
  }
  if (MUTATING_METHODS.has(method)) {
    headers['X-CSRF-Token'] = await fetchCsrfToken(request.signal)
  }

  let response: Response
  try {
    response = await fetch(`${apiBaseUrl()}${path}`, {
      method,
      headers,
      body: payload,
      credentials: 'same-origin',
      cache: 'no-store',
      signal: request.signal,
    })
  } catch {
    throw new ApiError(
      'network_error',
      'Unable to reach the server. Check your connection and try again.',
      0,
    )
  }

  if (response.status === 204) return undefined as T

  const body = await readJson(response)
  if (!response.ok) {
    const error = isObject(body) && isObject(body.error) ? body.error : {}
    const code = typeof error.code === 'string' ? error.code : 'request_failed'
    const message =
      typeof error.message === 'string'
        ? error.message
        : 'The request could not be completed.'
    throw new ApiError(code, message, response.status)
  }
  if (!isObject(body) || !('data' in body)) {
    throw new ApiError(
      'invalid_response',
      'The server returned an unexpected response.',
      response.status,
    )
  }
  return body.data as T
}
