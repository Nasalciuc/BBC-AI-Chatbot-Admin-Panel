// The one band at the top of the panel when something is off — pure, pinned
// by tests/bands.test.ts. One at a time; the most serious wins. A band never
// moves you off the page you're on.
//
// Only what the panel can know for sure: the browser's own online flag,
// /health not answering, the session's expiry, and another panel leading.
// No database, AI or CRM bands: /health doesn't report those.

export type BandKind = 'offline' | 'server' | 'session' | 'paused'

export interface BandInput {
  /** navigator.onLine */
  online: boolean
  /** Last /health check: true answered, false failed or 5xx, null not yet. */
  serverReachable: boolean | null
  /** Session expiry (ms), null when unknown. */
  expMs: number | null
  nowMs: number
  /** Inside the CRM dock the CRM signs you in, so no session band there. */
  embedded: boolean
  /** Another panel of yours (the CRM dock) is the one that rings. */
  notLeader: boolean
}

/** The session band shows in the last ten minutes. */
export const SESSION_WARN_MS = 10 * 60_000

export function pickBand(i: BandInput): BandKind | null {
  if (!i.online) return 'offline'
  if (i.serverReachable === false) return 'server'
  if (!i.embedded && i.expMs != null && i.expMs > i.nowMs && i.expMs - i.nowMs <= SESSION_WARN_MS) return 'session'
  if (i.notLeader) return 'paused'
  return null
}

export function bandText(kind: BandKind, o: { retryInS?: number; expMs?: number | null; nowMs?: number } = {}): { head: string; text: string } {
  switch (kind) {
    case 'offline':
      return { head: 'You’re offline.', text: 'Replies can’t be sent until you’re back. What you type is kept.' }
    case 'server':
      return {
        head: 'Can’t reach the server.',
        text: `${o.retryInS != null && o.retryInS > 0 ? `Trying again in ${o.retryInS} s.` : 'Trying again now.'} What you type is kept.`,
      }
    case 'session': {
      const left = o.expMs != null && o.nowMs != null ? Math.max(1, Math.ceil((o.expMs - o.nowMs) / 60_000)) : 10
      return {
        head: `Your session ends in ${left} min.`,
        text: 'Sign in again now and you come straight back here, with what you were typing.',
      }
    }
    case 'paused':
      return {
        head: 'This tab is paused.',
        text: 'The panel is active in the CRM, so chats ring there, not here. This tab wakes up by itself when the CRM tab closes.',
      }
  }
}

/** Seconds until the next /health check, counted from the last answer. */
export function secondsToNext(checkedAt: number, everyMs: number, nowMs: number): number {
  return Math.max(0, Math.ceil((checkedAt + everyMs - nowMs) / 1000))
}

/** Where "Sign in again" and a 401 bring you back to: the open chat
 *  reopens through ?highlight=, anything else is the address as it was. */
export function backTo(pathname: string, href: string, viewingConversationId: string | null): string {
  return pathname.startsWith('/chats') && viewingConversationId
    ? `/chats?highlight=${encodeURIComponent(viewingConversationId)}`
    : withoutSignInTokens(href)
}

/** A sign-in redirect without the CRM's one-time sign-in token in it
 *  (?sso_token=, legacy ?token=): it was already used, and a copy in the
 *  sign-in page's address would sit in the browser history. */
export function withoutSignInTokens(href: string): string {
  try {
    const u = new URL(href, 'https://panel.invalid')
    u.searchParams.delete('sso_token')
    u.searchParams.delete('token')
    return `${u.pathname}${u.search}${u.hash}`
  } catch {
    return '/'
  }
}
