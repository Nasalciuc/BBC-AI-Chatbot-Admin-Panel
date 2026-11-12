/**
 * Presence v2 — what the widget tells the server about the visitor being on the site.
 *
 * The first version only spoke while the chat was OPEN, and said "left" with a beacon on
 * pagehide that a killed tab or a phone put to sleep never sends. 30 days to 12 Nov 2026:
 * of 581 chats, 171 ended "online" and 263 "minimized", whatever the visitor really did.
 *
 * Now the widget sends three signals and the server decides (bbc-chatbot-api,
 * app/services/client_presence.py):
 *   beat     every 30 s while the PAGE is in view, chat open or closed, with the seconds
 *            since the visitor last moved, scrolled, typed or touched the page;
 *   hidden   when the page goes out of view: another tab or app, a locked phone. It is the
 *            last event a page reliably gets, on a phone above all (MDN, visibilitychange);
 *   leaving  on pagehide: the page closed, or the visitor followed a link (the next page
 *            beats within seconds, so the server waits before calling it a departure).
 *
 * Pure functions so the policy is testable without a DOM.
 */

export type PresenceSignal = 'beat' | 'hidden' | 'leaving'

/** A beat while the page is in view. Two missed beats read "no signal" on the server. */
export const BEAT_MS = 30_000

/** After this long with nobody touching the page the server already says "left", so the
 *  beats stop; the first touch afterwards beats at once. */
export const BEAT_IDLE_STOP_MS = 20 * 60_000

/** What counts as the visitor doing something on the page — anywhere on it, not only in the
 *  chat: someone reading an offer scrolls, someone comparing flights moves the mouse. */
export const ACTIVITY_EVENTS = ['pointerdown', 'pointermove', 'keydown', 'wheel', 'scroll', 'touchstart'] as const

export function idleSeconds(now: number, lastActivity: number): number {
  return Math.max(0, Math.floor((now - lastActivity) / 1000))
}

/** A beat goes out only while the page is in view and someone touched it in the last 20 min. */
export function shouldBeat(pageVisible: boolean, now: number, lastActivity: number): boolean {
  return pageVisible && now - lastActivity <= BEAT_IDLE_STOP_MS
}

interface NavigatorLike {
  userAgent?: string
  maxTouchPoints?: number
  userAgentData?: { mobile?: boolean }
}

/** A phone or a tablet: its browser is put in the background rather than left in a tab, so
 *  the server gives it 2 minutes out of view, not 20. iPadOS asks for the desktop site and
 *  says "Macintosh": a Mac with a touch screen is an iPad. */
export function isMobileDevice(nav: NavigatorLike | undefined): boolean {
  if (!nav) return false
  if (nav.userAgentData?.mobile === true) return true
  const ua = nav.userAgent || ''
  if (/Mobi|Android|iPhone|iPod|iPad/i.test(ua)) return true
  return /Macintosh/.test(ua) && (nav.maxTouchPoints ?? 0) > 1
}

/** The query of one signal: POST /api/chat/session/{id}/ping?<this>. No body, so a keepalive
 *  request at pagehide stays as small and as simple as it can be. */
export function signalQuery(
  kind: PresenceSignal,
  o: { chatOpen: boolean; idleS: number; mobile: boolean },
): string {
  const q = new URLSearchParams({ v: '2', kind, idle: String(Math.max(0, Math.floor(o.idleS))), m: o.mobile ? '1' : '0' })
  if (kind === 'beat') q.set('chat', o.chatOpen ? 'open' : 'closed')
  return q.toString()
}
