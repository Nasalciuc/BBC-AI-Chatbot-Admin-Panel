/**
 * Fetch wrapper that auto-injects the X-Visitor-Id header on write requests.
 * Read-only requests (GET) pass through without modification since the
 * ownership dependency only guards mutations.
 */

const VISITOR_KEY = 'bbc_visitor_id'

export function getVisitorId(): string | null {
  try { return localStorage.getItem(VISITOR_KEY) } catch { return null }
}

export function apiFetch(url: string, init?: RequestInit): Promise<Response> {
  const method = (init?.method || 'GET').toUpperCase()
  const isWrite = method !== 'GET' && method !== 'HEAD'

  if (!isWrite) return fetch(url, init)

  const visitorId = getVisitorId()
  if (!visitorId) return fetch(url, init)

  const headers = new Headers(init?.headers)
  headers.set('X-Visitor-Id', visitorId)
  return fetch(url, { ...init, headers })
}
