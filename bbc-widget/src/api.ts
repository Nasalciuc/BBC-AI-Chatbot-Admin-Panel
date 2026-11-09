/**
 * Fetch wrapper that injects the visitor's identity on every chat request:
 * X-Visitor-Id, and since PR-12 the server-signed X-Visitor-Token that
 * proves the id was issued by /chat/init or /chat/start (the server counts a
 * missing token for 7 days, then refuses it).
 */

const VISITOR_KEY = 'bbc_visitor_id'
const TOKEN_KEY = 'bbc_visitor_token'
/** Refresh when less than this is left of the token's 24 h. */
const REFRESH_BEFORE_S = 3600

export function getVisitorId(): string | null {
  try { return localStorage.getItem(VISITOR_KEY) } catch { return null }
}

export function getVisitorToken(): string | null {
  try { return localStorage.getItem(TOKEN_KEY) } catch { return null }
}

export function saveVisitorToken(token: unknown): void {
  if (typeof token !== 'string' || !token.startsWith('v1.')) return
  try { localStorage.setItem(TOKEN_KEY, token) } catch {}
}

export function clearVisitorToken(): void {
  try { localStorage.removeItem(TOKEN_KEY) } catch {}
}

/** Expiry (epoch seconds) of a v1 token, or 0 when it cannot be read. The
 *  widget only reads it to decide when to refresh — the server verifies. */
export function tokenExpiry(token: string | null): number {
  if (!token) return 0
  const parts = token.split('.')
  if (parts.length !== 5) return 0
  const exp = Number(parts[3])
  return Number.isFinite(exp) ? exp : 0
}

/** The token belongs to the visitor id we hold now (a reset id drops it). */
export function tokenMatches(token: string | null, visitorId: string | null): boolean {
  if (!token || !visitorId) return false
  return token.split('.')[1] === visitorId
}

export function needsRefresh(token: string | null, visitorId: string | null, nowS: number): boolean {
  if (!visitorId) return false
  if (!tokenMatches(token, visitorId)) return true
  return tokenExpiry(token) - nowS < REFRESH_BEFORE_S
}

export function apiFetch(url: string, init?: RequestInit): Promise<Response> {
  const visitorId = getVisitorId()
  if (!visitorId) return fetch(url, init)

  const headers = new Headers(init?.headers)
  headers.set('X-Visitor-Id', visitorId)
  const token = getVisitorToken()
  if (tokenMatches(token, visitorId)) headers.set('X-Visitor-Token', token as string)
  return fetch(url, { ...init, headers })
}

let refreshing: Promise<void> | null = null
/** The server issues no tokens (no secret configured): stop asking. */
let tokensOff = false

/** Make sure a fresh token is held before a long-lived call (the SSE stream
 *  puts it in its URL once). One refresh at a time; never throws. */
export function ensureVisitorToken(apiUrl: string, getTurnstile?: () => Promise<string | null>): Promise<void> {
  const visitorId = getVisitorId()
  if (tokensOff) return Promise.resolve()
  if (!needsRefresh(getVisitorToken(), visitorId, Date.now() / 1000)) return Promise.resolve()
  if (refreshing) return refreshing
  refreshing = (async () => {
    try {
      const old = getVisitorToken()
      // An old token of this visitor is traded in; only without one does the
      // server want Turnstile again — so only then is Cloudflare loaded.
      const turnstile_token = getTurnstile && !tokenMatches(old, visitorId) ? await getTurnstile() : null
      const res = await fetch(`${apiUrl}/api/chat/token`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(old ? { 'X-Visitor-Token': old } : {}),
        },
        body: JSON.stringify({ visitor_id: visitorId, turnstile_token }),
      })
      if (res.ok) {
        const t = (await res.json())?.visitor_token
        if (t == null) tokensOff = true
        else saveVisitorToken(t)
      }
    } catch { /* the server only counts a missing token during rollout */ }
    finally { refreshing = null }
  })()
  return refreshing
}
