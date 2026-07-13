/**
 * CRM iframe SSO (Option B) — accept a JWT from the parent CRM via postMessage.
 *
 * Parent contract:
 *   iframe.contentWindow.postMessage(
 *     { type: 'bbc-auth', token: '<jwt>' },
 *     'https://chat.buybusinessclass.com'  // panel origin
 *   )
 *
 * Token must be a JWT signed with our JWT_SECRET (same claims as /api/auth/login).
 * Origins must be BBC-owned (or listed in VITE_CRM_ORIGINS).
 */
import { useAuthStore } from '@/stores/auth-store'

const MSG_TYPE = 'bbc-auth'
const ACK_TYPE = 'bbc-auth-ack'

function parseJwtPayload(token: string): Record<string, unknown> | null {
  try {
    const base64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')
    return JSON.parse(atob(base64))
  } catch {
    return null
  }
}

function isAllowedCrmOrigin(origin: string): boolean {
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

/**
 * Install once at app boot. Safe if panel is not in an iframe (messages ignored).
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
  return () => window.removeEventListener('message', handler)
}
