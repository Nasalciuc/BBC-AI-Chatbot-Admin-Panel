/**
 * Fetch wrapper that injects the visitor's identity on every chat request:
 * X-Visitor-Id, and since PR-12 the server-signed X-Visitor-Token that
 * proves the id was issued by /chat/init or /chat/start (the server counts a
 * missing token for 7 days, then refuses it).
 */

import type { AgentTestSide } from './agentTest'
import type { ButtonKind, ChannelKind, ChatButton } from './channel'

const BUTTON_KINDS: readonly ButtonKind[] = ['sms', 'email', 'wait', 'change']

/** The server's buttons ({"kind", "label"}: app/services/card_words.py), each with a known kind and a label;
 *  anything else dropped. The widget acts on the kind, whatever the language of the label. */
export function readButtons(raw: unknown): ChatButton[] {
  if (!Array.isArray(raw)) return []
  return raw.flatMap((b): ChatButton[] => {
    if (!b || typeof b !== 'object') return []
    const { kind, label } = b as { kind?: unknown; label?: unknown }
    if (typeof kind !== 'string' || !BUTTON_KINDS.includes(kind as ButtonKind)) return []
    if (typeof label !== 'string' || !label.trim()) return []
    return [{ kind: kind as ButtonKind, label: label.trim().slice(0, 60) }]
  })
}

/** One channel button (SMS or email), or null. */
export function readChannel(raw: unknown): (ChatButton & { kind: ChannelKind }) | null {
  const [b] = readButtons([raw])
  return b && (b.kind === 'sms' || b.kind === 'email') ? (b as ChatButton & { kind: ChannelKind }) : null
}

const VISITOR_KEY = 'bbc_visitor_id'
const TOKEN_KEY = 'bbc_visitor_token'
/** Refresh when less than this is left of the token's 24 h. */
const REFRESH_BEFORE_S = 3600

export function getVisitorId(): string | null {
  try { return localStorage.getItem(VISITOR_KEY) } catch { return null }
}

/** Generate a visitor_id (crypto.randomUUID with fallback). */
export function makeVisitorId(): string {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID()
  // Fallback for older browsers — not cryptographically strong but unique enough
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = (Math.random() * 16) | 0
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16)
  })
}

/** Ensure a visitor_id exists in localStorage, creating one if needed. */
export function ensureVisitorId(): string {
  const existing = getVisitorId()
  if (existing) return existing
  const id = makeVisitorId()
  try { localStorage.setItem(VISITOR_KEY, id) } catch {}
  return id
}

/** What the card's yes is answered with at once, when the card knew the whole trip (A2): the server's
 *  words and the buttons under them (app/services/card_words.py, confirm). On the field side of the owner's
 *  contact test (22 Nov, app/services/contact_field_test.py) also the field the chat opens with them, and
 *  "Get a fare quote" on a card that knew no whole trip gets one too, contact first. */
export interface OfferConfirm {
  message: string
  buttons: ChatButton[]
  field?: ChannelKind
}

/** The owner's contact test: the contact field at once after the yes ("field"), or the choice first ("choice"). */
export type ContactTestSide = 'field' | 'choice'

/** The server's answer to "open the chat now?" (bbc-chatbot-api app/services/proactive.py, offer). */
export interface ProactiveOffer {
  show: boolean
  /** "proactive", "control", "off" (the feature switched off on the server) or "trigger_off" (this moment
   *  alone switched off: triggers.ts, TRIGGER_OFF_ARM). */
  arm: string
  message: string | null
  quick_replies: string[]
  /** Which words the server chose, when it chose a variant: how the note asked its first question (the
   *  note's bandit), the quote card's chat instead of the form ("contact"), the row beside the refused form
   *  ("row") or the Kayak card's tested sentence ("beat"). */
  variant?: string
  /** The instant answer to the card's yes (A2). */
  confirm?: OfferConfirm
  /** The card's own channel button (C1) and the pill after "Not now" (C2). */
  channel?: ChatButton & { kind: ChannelKind }
  /** The row's two buttons beside the refused quote form (D1). */
  buttons?: ChatButton[]
  /** The language the server composed the words in ("es", "fr"); absent: English. */
  lang?: string
  /** ADR A3: the phone card's title (the trip) and its one question; the chat opens with `message`. */
  headline?: string
  card_text?: string
  /** The owner's agent-first test (agentTest.ts): the visitor's side, on every answer while the test runs. */
  test?: AgentTestSide
  /** arm "agent_first": Alex waits for an agent; ask again after this many seconds (the moment is given back). */
  retry_after_s?: number
  /** ...and when he comes by the server's clock (unix seconds), for the beat. */
  alex_at?: number
  /** arm "agent_first" without a wait: why the card doesn't come ("greeted", "declined"). */
  reason?: string
  /** The card comes because Alex's wait ran out with no hello. */
  after_hold?: boolean
  /** The owner's contact test: this visitor's side, on the cards it changes (sent back at /chat/init). */
  contact_test?: ContactTestSide
}

/** The longest card title the server sends (app/services/proactive.py, TITLE_MAX): the same number. */
export const TITLE_MAX = 240

/** A line of the server's for the card: a non-empty string up to `max` characters, else nothing. */
function shortText(v: unknown, max: number): string | null {
  return typeof v === 'string' && v.trim() && v.length <= max ? v.trim() : null
}

/** A variant name as the server writes them ("open", "places", "contact"): anything else is dropped. */
export function offerVariant(v: unknown): string | undefined {
  return typeof v === 'string' && /^[a-z][a-z_]{0,23}$/.test(v) ? v : undefined
}

/** Why an offer brought no answer: our time ran out, the request failed on the way, the server said no
 *  with a status, or its reply was not an offer. */
export type OfferFail = 'timeout' | 'network' | 'http' | 'shape'

/** The seconds an agent-first "wait" answer gives (agentTest.ts waitSeconds; the server's cap, 600): this file
 *  imports nothing but types, so the tests run it in node. */
export function offerWait(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) && v >= 1 && v <= 600 ? Math.ceil(v) : null
}

/** How an offer request went, for the measurements: how long it took, and why no answer came (null: one did). */
export interface OfferTiming {
  ms: number
  fail: OfferFail | null
}

/** The measurement properties of an offer request (PostHog): `offer_ms` to the nearest 10 ms, and
 *  `offer_fail` when no answer came. Nothing when the request never reported. */
export function offerMeasures(t: OfferTiming | undefined): { offer_ms?: number; offer_fail?: OfferFail } {
  if (!t || !Number.isFinite(t.ms)) return {}
  return { offer_ms: Math.max(0, Math.round(t.ms / 10) * 10), ...(t.fail ? { offer_fail: t.fail } : {}) }
}

/**
 * Ask whether to open the chat by itself now, and with what words (POST /chat/proactive/offer — it
 * writes nothing). No answer within `timeoutMs`, an error or a reply of the wrong shape: null, and
 * nothing is shown. `onTiming` hears how it went, once, whatever the outcome.
 */
export async function fetchProactiveOffer(
  apiUrl: string,
  /** `site`: bbc | bct, so the server's opening names the brand the visitor is on. `refused`: on the quote
   *  moment, the kinds of the fields the form refused (siteSignals FIELD_KINDS, never a value). */
  body: {
    visitor_id: string; trigger: string; trip: unknown; site: string; refused?: string[]
    /** The browser's language, the screen, and a row beside the refused form (proactive.ts). */
    lang?: string; device?: 'phone' | 'computer'; place?: 'form'
    /** This widget opens the contact field with the yes's answer: it takes part in the owner's contact test. */
    opens_field?: boolean
  },
  timeoutMs: number,
  onTiming?: (t: OfferTiming) => void,
): Promise<ProactiveOffer | null> {
  const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null
  let timedOut = false
  const timer = setTimeout(() => { timedOut = true; ctrl?.abort() }, timeoutMs)
  const startedAt = Date.now()
  const done = <T>(fail: OfferFail | null, value: T): T => {
    try { onTiming?.({ ms: Date.now() - startedAt, fail }) } catch { /* a measurement never costs the answer */ }
    return value
  }
  try {
    const res = await apiFetch(`${apiUrl}/api/chat/proactive/offer`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: ctrl?.signal,
    })
    if (!res.ok) return done('http', null)
    const data = await res.json()
    if (!data || typeof data.show !== 'boolean' || typeof data.arm !== 'string') return done('shape', null)
    const replies = Array.isArray(data.quick_replies)
      ? data.quick_replies.filter((r: unknown): r is string => typeof r === 'string' && r.trim() !== '')
      : []
    const variant = offerVariant(data.variant)
    const confirmMessage = typeof data.confirm?.message === 'string' && data.confirm.message.trim()
      ? data.confirm.message : null
    const confirmButtons = readButtons(data.confirm?.buttons)
    const confirmField: ChannelKind | null = data.confirm?.field === 'sms' || data.confirm?.field === 'email'
      ? data.confirm.field : null
    const channel = readChannel(data.channel)
    const buttons = readButtons(data.buttons)
    // ADR A3: both or neither — a title without its question (or the reverse) would be half a card. The title's
    // limit is the server's own cap (proactive.TITLE_MAX): a title it sends is one this shows (CodeRabbit #272).
    const headline = shortText(data.headline, TITLE_MAX)
    const cardText = shortText(data.card_text, 240)
    return done(null, {
      show: data.show,
      arm: data.arm,
      message: typeof data.message === 'string' && data.message.trim() ? data.message : null,
      quick_replies: replies,
      ...(variant ? { variant } : {}),
      ...(confirmMessage && confirmButtons.length ? {
        confirm: { message: confirmMessage, buttons: confirmButtons, ...(confirmField ? { field: confirmField } : {}) },
      } : {}),
      ...(channel ? { channel } : {}),
      ...(buttons.length ? { buttons } : {}),
      ...(data.lang === 'es' || data.lang === 'fr' ? { lang: data.lang } : {}),
      ...(headline && cardText ? { headline, card_text: cardText } : {}),
      ...(data.test === 'agent' || data.test === 'alex' ? { test: data.test as AgentTestSide } : {}),
      ...(offerWait(data.retry_after_s) !== null ? { retry_after_s: offerWait(data.retry_after_s) as number } : {}),
      ...(Number.isInteger(data.alex_at) && data.alex_at > 0 ? { alex_at: data.alex_at as number } : {}),
      ...(data.reason === 'greeted' || data.reason === 'declined' || data.reason === 'waiting' ? { reason: data.reason } : {}),
      ...(data.after_hold === true ? { after_hold: true } : {}),
      ...(data.contact_test === 'field' || data.contact_test === 'choice'
        ? { contact_test: data.contact_test as ContactTestSide } : {}),
    })
  } catch (e) {
    // A timeout or a network error is a "no": the visitor sees nothing. So is a reply that is not JSON (a
    // proxy's HTML page): res.json() refuses it with a SyntaxError, and that is the reply's shape, not the line.
    return done(timedOut ? 'timeout' : e instanceof SyntaxError ? 'shape' : 'network', null)
  } finally {
    clearTimeout(timer)
  }
}

/** B1 (ADR-6d): the one message for a contactless client who is leaving, once, from the server — which says
 *  whether it applies (no contact, no agent's word yet, never said). `keepalive`: the page may be going. */
export interface BeforeYouGo {
  show: boolean
  message?: string
  buttons?: ChatButton[]
  id?: string
  created_at?: string
}

export async function askBeforeYouGo(apiUrl: string, convId: string, keepalive = false): Promise<BeforeYouGo | null> {
  try {
    const res = await apiFetch(`${apiUrl}/api/chat/session/${convId}/before-you-go`, { method: 'POST', keepalive })
    if (!res.ok) return null
    const data = await res.json()
    if (!data || data.show !== true || typeof data.message !== 'string' || typeof data.id !== 'string') {
      return { show: false }
    }
    return {
      show: true, message: data.message, buttons: readButtons(data.buttons), id: data.id,
      ...(typeof data.created_at === 'string' ? { created_at: data.created_at } : {}),
    }
  } catch {
    return null
  }
}

/**
 * Opens the connection to the API before the first request needs it (name lookup, TCP and TLS done ahead),
 * so the card's answer and a tap on the corner button do not wait for them (triggers.ts, OFFER_TIMEOUT_MS:
 * on 16-18 Nov the answers that came too late spent their time there, not on the server). Once per page; an
 * address that is not one, or a page without a head, does nothing.
 */
export function preconnect(apiUrl: string): void {
  try {
    const origin = new URL(apiUrl).origin
    if (!/^https?:\/\//.test(origin)) return
    const already = Array.from(document.querySelectorAll('link[rel="preconnect"]')).some((l) => {
      const link = l as HTMLLinkElement
      return link.href.replace(/\/$/, '') === origin && link.crossOrigin === 'anonymous'
    })
    if (already) return
    const link = document.createElement('link')
    link.rel = 'preconnect'
    link.href = origin
    // fetch() sends the API no cookies (credentials: same-origin, another site): the connection it takes is
    // the anonymous one, so that is the one opened ahead.
    link.crossOrigin = 'anonymous'
    document.head.appendChild(link)
  } catch { /* not an address, or no document: the first request opens its own */ }
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

/**
 * Reserved route of the v0 widget. NEVER call it from this build — it is a
 * security control (PR-15; the private runbook explains it). It ships in the
 * bundle on purpose, through entry.tsx.
 */
export const TRANSCRIPT_EXPORT_V0 = '/api/chat/v0/transcript-export'

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
