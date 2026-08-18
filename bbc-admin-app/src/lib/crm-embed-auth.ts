/**
 * CRM SSO — two entry points into the panel session:
 *
 * 1. URL token (docs/chatbot-sso.md — the mechanism the CRM team built):
 *    the CRM opens `https://chat.buybusinessclass.com/?sso_token=<crm-signed-jwt>`
 *    (legacy `?token=` still accepted outside public-token routes until the
 *    CRM side renames). `tryCrmUrlTokenLogin()` exchanges that CRM token at
 *    `POST /api/auth/sso/crm-exchange` for OUR session JWT, logs in, then strips
 *    the param from the URL — on success only.
 *
 * 2. postMessage (kept for a possible future same-domain/iframe handshake):
 *    parent posts { type: 'bbc-auth', token: '<our jwt>' } into the iframe.
 *    Token must be a JWT signed with our JWT_SECRET (same claims as /api/auth/login).
 *    Origins must be BBC-owned (or listed in VITE_CRM_ORIGINS).
 */
import { useAuthStore } from '@/stores/auth-store'

const MSG_TYPE = 'bbc-auth'
const ACK_TYPE = 'bbc-auth-ack'

const API_BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000'

function parseJwtPayload(token: string): Record<string, unknown> | null {
  try {
    const base64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')
    return JSON.parse(atob(base64))
  } catch {
    return null
  }
}

export function isAllowedCrmOrigin(origin: string): boolean {
  try {
    const url = new URL(origin)
    if (url.protocol !== 'https:' && url.hostname !== 'localhost') return false
    const host = url.hostname.toLowerCase()
    if (host === 'buybusinessclass.com' || host.endsWith('.buybusinessclass.com')) {
      return true
    }
    if (host === 'localhost' || host === '127.0.0.1') return true
  } catch {
    return false
  }

  const extra = (import.meta.env.VITE_CRM_ORIGINS as string | undefined) || ''
  return extra
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
    .includes(origin)
}

function applyToken(token: string): boolean {
  if (!token || token.split('.').length !== 3) return false
  const claims = parseJwtPayload(token)
  if (!claims) return false
  if (typeof claims.exp === 'number' && claims.exp * 1000 < Date.now()) return false

  const { auth } = useAuthStore.getState()
  auth.setAccessToken(token)
  auth.setUser({
    accountNo: (claims.sub as string) || '',
    email: (claims.email as string) || '',
    name: (claims.name as string) || '',
    role: (claims.role as string) || 'sales',
    tunnelScope: (claims.tunnel_scope as string) || '',
    exp: typeof claims.exp === 'number' ? claims.exp * 1000 : Date.now() + 86400000,
    avatar_url: (claims.avatar_url as string) || null,
    phone: (claims.phone as string) || '',
  })
  return true
}

// Relative import with extension on purpose — the pure routing rules are
// node-tested directly (path aliases don't resolve under node --test).
import { pickSsoToken } from './sso-token-routing.ts'

/**
 * URL-token SSO (docs/chatbot-sso.md). Reads `?sso_token=` (or the legacy
 * `?token=` outside public-token routes), exchanges the CRM-signed JWT for OUR
 * session JWT via the backend, logs in, and strips the param from the URL —
 * ON SUCCESS ONLY. A failed exchange leaves the URL intact: stripping on 401
 * is what destroyed invite links, and it also erases the evidence needed to
 * retry or debug. Returns true on success; never throws.
 */
export async function tryCrmUrlTokenLogin(): Promise<boolean> {
  let picked: [string, string] | null = null
  try {
    picked = pickSsoToken(window.location.pathname, window.location.search)
  } catch {
    return false
  }
  if (!picked) return false
  const [param, token] = picked

  try {
    const res = await fetch(`${API_BASE}/api/auth/sso/crm-exchange`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
      signal: AbortSignal.timeout(8000),
    })
    if (!res.ok) {
      // eslint-disable-next-line no-console
      console.warn('CRM SSO exchange failed:', res.status)
      return false
    }
    const data = await res.json()
    // data.token is OUR issued JWT (same claims as /api/auth/login) — reuse the
    // exact same store-application path as the postMessage receiver.
    const ok = typeof data?.token === 'string' && applyToken(data.token)
    if (ok) stripTokenFromUrl(param)
    return ok
  } catch (e) {
    // eslint-disable-next-line no-console
    console.warn('CRM SSO exchange error:', e)
    return false
  }
}

function stripTokenFromUrl(param: string): void {
  try {
    const url = new URL(window.location.href)
    if (!url.searchParams.has(param)) return
    url.searchParams.delete(param)
    window.history.replaceState({}, '', url.toString())
  } catch {
    /* non-browser / opaque — ignore */
  }
}

/**
 * Install once at app boot. Safe if panel is not in an iframe (messages ignored).
 *
 * Also attempts URL-token SSO immediately (the CRM team's ?token= flow); on
 * success it invokes `onAuthed` just like the postMessage path. This keeps the
 * bootstrap wiring in one place — callers only need to call installCrmEmbedAuth().
 */
export function installCrmEmbedAuth(onAuthed?: () => void): () => void {
  const handler = (event: MessageEvent) => {
    if (!isAllowedCrmOrigin(event.origin)) return
    const data = event.data
    if (!data || typeof data !== 'object' || data.type !== MSG_TYPE) return
    if (typeof data.token !== 'string') return

    const ok = applyToken(data.token)
    try {
      event.source?.postMessage?.(
        { type: ACK_TYPE, ok },
        { targetOrigin: event.origin }
      )
    } catch {
      /* parent may be cross-origin opaque */
    }
    if (ok) onAuthed?.()
  }

  window.addEventListener('message', handler)

  // Fire-and-forget URL-token exchange (CRM ?token= flow). Non-blocking.
  void tryCrmUrlTokenLogin().then((ok) => {
    if (ok) onAuthed?.()
  })

  return () => window.removeEventListener('message', handler)
}
