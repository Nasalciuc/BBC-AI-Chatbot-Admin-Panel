/**
 * The visit registry, the widget's pure part (contracts/live.md "The visit registry"; the network part is
 * visits.ts). What a page's snapshot says: the page, where the visitor came from, the screen, the trip as codes and
 * dates, the widget's events on the page as short words, the site's steps, the card and what came of it. Never what
 * the visitor typed, a name, an e-mail or a phone: bbc-live drops anything outside these shapes again.
 *
 * No imports but types, so the tests run it in node (tests/visits.test.mjs).
 */

import type { TripPayload } from './tripContext'

/** The events a page keeps (bbc-live keeps as many: VisitEvents). */
export const MAX_EVENTS = 40
/** The site's steps a page sends (bbc-live keeps as many: VisitSteps). */
export const MAX_STEPS = 30

/** The widget's events the registry keeps, as a short word and the properties that qualify it, in order. Anything
 *  not listed is not kept. */
export const VISIT_EVENTS: Readonly<Record<string, readonly string[]>> = {
  proactive_eligible: ['moment', 'trigger', 'arm'],
  proactive_shown: ['card', 'trigger', 'surface'],
  proactive_dismissed: ['card_no', 'how'],
  proactive_engaged: ['card_yes', 'reply'],
  proactive_pill_shown: ['pill', 'channel'],
  proactive_pill_opened: ['pill_open'],
  proactive_pill_closed: ['pill_close'],
  proactive_popup_held: ['popup_held'],
  proactive_note_unanswered: ['note_unanswered'],
  proactive_quote_unrecognized: ['quote_unrecognized'],
  live_hello_shown: ['hello', 'where'],
  live_hello_replied: ['hello_yes', 'where'],
  live_hello_dismissed: ['hello_no', 'where'],
  chat_tunnel_selected: ['chat_open', 'tunnel', 'via'],
  chat_quick_reply_selected: ['chat_reply'],
  chat_message_sent: ['chat_msg'],
  chat_message_send_failed: ['chat_msg_failed'],
  chat_channel_opened: ['channel_open', 'channel'],
  chat_channel_given: ['contact', 'channel'],
  contact_form_submitted: ['form_sent', 'tunnel'],
  call_initiated: ['call', 'entry_point'],
  chat_closed: ['chat_close'],
  chat_before_you_go: ['before_you_go'],
  widget_large_view: ['large_view'],
}

const WORD = /^[a-z0-9_]{1,24}$/

/** One event as the registry keeps it: "card:kayak:card", "contact:sms", "chat_msg". Qualifiers are words from the
 *  widget's own lists, never a value someone typed: one that isn't a word ends the token there. Null for an event
 *  the registry doesn't keep. */
export function eventToken(event: string, props: Record<string, unknown> | undefined): string | null {
  const spec = VISIT_EVENTS[event]
  if (!spec) return null
  const [word, ...keys] = spec
  const parts = [word]
  for (const k of keys.slice(0, 2)) {
    const v = props?.[k]
    const s = typeof v === 'string' ? v.toLowerCase() : typeof v === 'boolean' ? String(v) : ''
    if (!WORD.test(s)) break
    parts.push(s)
  }
  return parts.join(':')
}

/** The page's events with one more: the same one again counts ("chat_msg*3"), at most MAX_EVENTS. */
export function addEvent(list: readonly string[], token: string): string[] {
  const out = [...list]
  const last = out[out.length - 1]
  if (last) {
    const [name, n] = last.split('*')
    if (name === token) {
      out[out.length - 1] = `${token}*${Math.min((Number(n) || 1) + 1, 999)}`
      return out
    }
  }
  if (out.length < MAX_EVENTS) out.push(token)
  return out
}

export type CardState = 'shown' | 'dismissed' | 'engaged'

/** The card of this visit as the registry tells it (kept for the visit's pages: visits.ts, sessionStorage). */
export interface VisitCard {
  trigger: string
  variant: string
  surface: string
  state: CardState
  /** ms since the epoch, by this browser's clock: the snapshot sends how long ago, bbc-live keeps its own time. */
  at: number
  answer?: string
  answeredAt?: number
}

// Every moment the widget shows a card for: `social` is the Meta ads' card (CodeRabbit #282, round 3).
const TRIGGERS = new Set(['kayak', 'ads', 'social', 'quote', 'reader', 'exit', 'note'])
const SURFACES = new Set(['card', 'panel', 'row'])

/** The card after an event: the first card of the visit is the card; its answer only moves forward (an answer
 *  stays an answer, whatever is closed afterwards), and only its own answer counts: a note that opened later and
 *  was closed or answered is not what came of the card (CodeRabbit #282, round 3). Null stays null until a card
 *  shows. */
export function cardAfter(card: VisitCard | null, event: string, props: Record<string, unknown> | undefined,
  nowMs: number): VisitCard | null {
  const s = (k: string) => (typeof props?.[k] === 'string' ? (props[k] as string) : '')
  if (event === 'proactive_shown') {
    if (card || !TRIGGERS.has(s('trigger'))) return card
    return { trigger: s('trigger'), variant: WORD.test(s('variant').toLowerCase()) ? s('variant').toLowerCase() : '',
      surface: SURFACES.has(s('surface')) ? s('surface') : '', state: 'shown', at: nowMs }
  }
  if (!card || card.state === 'engaged' || s('trigger') !== card.trigger) return card
  if (event === 'proactive_engaged') {
    return { ...card, state: 'engaged', answer: WORD.test(s('reply')) ? s('reply') : '', answeredAt: nowMs }
  }
  if (event === 'proactive_dismissed' && card.state === 'shown') {
    return { ...card, state: 'dismissed', answer: WORD.test(s('how')) ? s('how') : '', answeredAt: nowMs }
  }
  return card
}

/** A kept card read back, or null when it isn't one. */
export function readVisitCard(raw: string | null): VisitCard | null {
  let v: unknown = null
  try { v = raw ? JSON.parse(raw) : null } catch { return null }
  if (!v || typeof v !== 'object') return null
  const c = v as Record<string, unknown>
  if (c.state !== 'shown' && c.state !== 'dismissed' && c.state !== 'engaged') return null
  if (typeof c.trigger !== 'string' || !TRIGGERS.has(c.trigger)) return null
  if (typeof c.at !== 'number' || !Number.isFinite(c.at) || c.at <= 0) return null
  const card: VisitCard = { trigger: c.trigger, variant: typeof c.variant === 'string' ? c.variant : '',
    surface: typeof c.surface === 'string' ? c.surface : '', state: c.state, at: c.at }
  if (typeof c.answer === 'string') card.answer = c.answer
  if (typeof c.answeredAt === 'number' && Number.isFinite(c.answeredAt)) card.answeredAt = c.answeredAt
  return card
}

/** The card kept for this tab, with the browser's visit it belongs to (visitCount.ts): a card from an earlier visit
 *  is not this visit's, and the new visit's first card must be free to be the card (CodeRabbit #282, round 3). */
export function keptCardFor(raw: string | null, visit: number): VisitCard | null {
  let v: unknown = null
  try { v = raw ? JSON.parse(raw) : null } catch { return null }
  if (!v || typeof v !== 'object') return null
  const k = v as { visit?: unknown; card?: unknown }
  if (typeof k.visit !== 'number' || k.visit !== visit || !visit) return null
  return readVisitCard(JSON.stringify(k.card ?? null))
}

/** What keptCardFor reads back. */
export function cardToKeep(card: VisitCard, visit: number): string {
  return JSON.stringify({ visit, card })
}

const COMPARISON = new Set(['kayak', 'skyscanner', 'momondo', 'kiwi', 'cheapflights', 'expedia'])
const PAID = new Set(['cpc', 'ppc', 'paid', 'paidsearch', 'paid_search', 'paidsocial', 'paid_social', 'cpm'])
const SEARCH = /(^|\.)(google|bing|yahoo|duckduckgo|ecosia|yandex|baidu|brave)\.[a-z.]+$/
const META = new Set(['facebook', 'fb', 'instagram', 'ig', 'meta'])

/** The host of an address, lowercase, or ''. */
export function hostOf(url: string | undefined): string {
  if (!url) return ''
  try { return new URL(url).hostname.toLowerCase() } catch { return '' }
}

/** Where the visitor came from, one word: a comparison site, an ad (Google, Meta, Bing, another), a search engine,
 *  another site, or nothing (direct). `utm` as utm.ts getUtm keeps it: with marketing consent off it names no
 *  source, and the visit reads as direct or by its referrer only. */
export function channelOf(utm: Record<string, string | undefined>, referrer: string, siteHost: string): string {
  const src = (utm.utm_source || '').trim().toLowerCase()
  const medium = (utm.utm_medium || '').trim().toLowerCase()
  if (COMPARISON.has(src) || utm.kayak_click_id || utm.kclid || utm.skyscanner_click_id) return 'kayak'
  if (utm.gclid || (src.startsWith('google') && PAID.has(medium))) return 'google'
  if (utm.fbclid || META.has(src)) return 'meta'
  if (utm.msclkid || ((src === 'bing' || src === 'microsoft') && PAID.has(medium))) return 'bing'
  if (PAID.has(medium)) return 'ads'
  const ref = hostOf(referrer)
  const own = siteHost.toLowerCase().replace(/^www\./, '')
  if (!ref || (own && (ref === own || ref.endsWith('.' + own)))) return src ? 'other' : 'direct'
  return SEARCH.test(ref) ? 'search' : 'referral'
}

/** A snapshot's body (POST /v1/visit). Only what bbc-live keeps; it checks all of it again. */
export function visitBody(o: {
  pageId: string; path: string; pageType?: string | null; pageMs: number; seenMs: number
  trip: TripPayload | null; utm: Record<string, string | undefined>; referrer: string; siteHost: string
  phone: boolean; site: string; lang?: string; tz?: string; country?: string; phSession?: string
  events: readonly string[]; steps: string; card: VisitCard | null; hasChat: boolean; conversationId?: string | null
  nowMs: number; pass?: string
}): Record<string, unknown> {
  const path = (o.path || '/').split(/[?#]/)[0].slice(0, 120) || '/'
  const t = o.trip
  const code = (v?: string) => (v && /^[A-Z]{3}$/.test(v.toUpperCase()) ? v.toUpperCase() : undefined)
  const day = (v?: string) => (v && /^\d{4}-\d{2}-\d{2}$/.test(v) ? v : undefined)
  const secs = (ms: number) => Math.max(0, Math.min(86_400, Math.floor(ms / 1000)))
  const src = (o.utm.utm_source || '').trim().toLowerCase()
  const b: Record<string, unknown> = {
    page_id: o.pageId, page: path.startsWith('/') ? path : '/', page_s: secs(o.pageMs), seen_s: secs(o.seenMs),
    source: COMPARISON.has(src) || o.utm.kayak_click_id || o.utm.kclid || o.utm.skyscanner_click_id ? 'kayak'
      : o.utm.gclid || o.utm.msclkid || o.utm.fbclid || PAID.has((o.utm.utm_medium || '').trim().toLowerCase()) ? 'ads'
        : 'site',
    channel: channelOf(o.utm, o.referrer, o.siteHost),
    device: o.phone ? 'phone' : 'computer', site: o.site, has_chat: o.hasChat,
    events: o.events.slice(0, MAX_EVENTS).join(';'), steps: o.steps,
  }
  if (o.pageType) b.page_type = o.pageType
  for (const k of ['utm_source', 'utm_medium', 'utm_campaign'] as const) {
    const v = (o.utm[k] || '').trim()
    if (v && !v.includes('@') && v.length <= 80) b[k] = v
  }
  const ref = hostOf(o.referrer)
  if (ref) b.referrer = ref
  if (t) {
    const pax = (t.adults || 0) + (t.children || 0) + (t.infants || 0)
    const from = code(t.origin_code), to = code(t.destination_code), depart = day(t.depart_date), ret = day(t.return_date)
    if (from) b.from = from
    if (to) b.to = to
    if (depart) b.depart = depart
    if (ret) b.return = ret
    if (t.cabin) b.cabin = t.cabin
    if (pax >= 1 && pax <= 9) b.pax = pax
  }
  if (o.lang) b.lang = o.lang
  if (o.tz) b.tz = o.tz
  if (o.country && /^[A-Z]{2}$/.test(o.country)) b.country = o.country
  if (o.phSession) b.ph_session = o.phSession
  if (o.conversationId && /^[0-9a-f-]{36}$/.test(o.conversationId)) b.conversation_id = o.conversationId
  const c = o.card
  if (c) {
    const card: Record<string, unknown> = { trigger: c.trigger, variant: c.variant, surface: c.surface, state: c.state,
      s: secs(o.nowMs - c.at) }
    if (c.state !== 'shown' && c.answeredAt) {
      card.answer = c.answer || ''
      card.answer_s = secs(o.nowMs - c.answeredAt)
    }
    b.card = card
  }
  if (o.pass) b.pass = o.pass
  return b
}

/** A random page id (bbc-live: 6 to 32 letters, digits, - or _). */
export function newPageId(rand: () => number = Math.random): string {
  let id = 'p'
  while (id.length < 14) id += Math.floor(rand() * 36).toString(36)
  return id
}
