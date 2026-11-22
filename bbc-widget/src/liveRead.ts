/**
 * "On the site now", the widget's pure part (contracts/live.md): reading the API's config and bbc-live's hello,
 * and building a beat. No imports but types, so the tests run it in node (tests/live.test.mjs); the hook that
 * talks to the network is live.ts.
 */

import type { TripPayload } from './tripContext'

export interface LiveConfig {
  enabled: boolean
  url?: string
  after_s?: number
  beat_s?: number
  hello?: boolean
  /** The visitor's pass to bbc-live (b1…), sent as X-Live-Pass; kept to renew it on the next page. */
  pass?: string
  /** No pass for now (the API's store unreachable, the API down or busy): ask again later, don't stop. */
  later?: boolean
  /** The owner's agent-first test (agentTest.ts): this visitor's side, on/off answers alike while it runs. */
  test?: 'agent' | 'alex'
}

export interface LiveHello {
  id: string
  agent: string
  text: string
  tap: string
  /** ms since the epoch */
  expiresAt: number
}

export interface Beat {
  page: string
  from?: string
  to?: string
  depart?: string
  return?: string
  cabin?: string
  pax?: number
  source: 'kayak' | 'ads' | 'site'
  device: 'phone' | 'computer'
  site: string
  chat_open: boolean
  has_chat: boolean
  /** What the site says the page is (siteSignals.ts PAGE_TYPES), when it says. */
  page_type?: string
  /** The visitor's last steps on the site (siteSignals.ts activityLine): codes, dates, steps, kinds. */
  activity?: string
  /** The agent-first test: when Alex's card may come (unix seconds), while he waits for an agent. */
  alex_at?: number
}

/** siteSignals.ts PAGE_TYPES, here because this file imports nothing but types (tests/live.test.mjs pins them). */
export const BEAT_PAGE_TYPES: readonly string[] = [
  'home', 'route', 'destination', 'origin', 'airline', 'deals', 'search',
  'flight', 'quote', 'thank_you', 'blog', 'about', 'legal', 'other',
]

const AFTER_S = 20
const BEAT_S = 15
const MAX_TEXT = 300
const COMPARISON = new Set(['kayak', 'skyscanner', 'momondo', 'kiwi', 'cheapflights', 'expedia'])
const CABINS = new Set(['economy', 'premium', 'business', 'first'])
const PASS = /^b1\.[A-Za-z0-9_-]{1,400}\.[0-9a-f]{64}$/

/** The config the API answered (POST /chat/live), or "off" for anything else: on means a place and a pass.
 *  `later: true` is the API saying it can't hand a pass for now: kept apart from off (CodeRabbit #273). */
export function readConfig(raw: unknown): LiveConfig {
  if (!raw || typeof raw !== 'object') return { enabled: false }
  const r = raw as Record<string, unknown>
  // The agent-first test's side rides on every answer, "off" included: Alex's side is never listed.
  const side: LiveConfig['test'] = r.test === 'agent' || r.test === 'alex' ? r.test : undefined
  const test = side ? { test: side } : {}
  if (r.later === true) return { enabled: false, later: true, ...test }
  if (r.enabled !== true || typeof r.url !== 'string' || !/^https?:\/\/[^\s/]+/.test(r.url)) return { enabled: false, ...test }
  if (typeof r.pass !== 'string' || !PASS.test(r.pass)) return { enabled: false, ...test }
  const n = (v: unknown, d: number) => (typeof v === 'number' && v >= 1 && v <= 600 ? v : d)
  return { enabled: true, url: r.url.replace(/\/+$/, ''), after_s: n(r.after_s, AFTER_S), beat_s: n(r.beat_s, BEAT_S),
    hello: r.hello === true, pass: r.pass, ...test }
}

/** A kept pass worth showing the API again: the shape only (the API checks the rest). */
export function keptPass(raw: string | null): string | null {
  return raw && PASS.test(raw) ? raw : null
}

/** The first hello in bbc-live's answer, held to the contract's shape (contracts/live_inbox_message.json); null
 *  for anything else, or one that ran out. */
export function readHello(raw: unknown, nowMs: number): LiveHello | null {
  const list = raw && typeof raw === 'object' ? (raw as { messages?: unknown }).messages : null
  if (!Array.isArray(list)) return null
  for (const m of list) {
    if (!m || typeof m !== 'object') continue
    const r = m as Record<string, unknown>
    const s = (k: string) => (typeof r[k] === 'string' ? (r[k] as string) : '')
    const expiresAt = Date.parse(s('expires_at'))
    const text = s('text').trim()
    // Characters as the API counts them (Python's len: code points), not UTF-16 units: 200 emoji are 200.
    if (r.kind !== 'hello' || !s('id') || !s('tap').startsWith('t1.') || !text || [...text].length > MAX_TEXT) continue
    if (!Number.isFinite(expiresAt) || expiresAt <= nowMs) continue
    return { id: s('id'), agent: s('agent').trim().slice(0, 40) || 'Your travel agent', text, tap: s('tap'), expiresAt }
  }
  return null
}

const PAID_MEDIUMS = new Set(['cpc', 'ppc', 'paid', 'paidsearch', 'paid_search', 'paidsocial', 'paid_social', 'cpm'])

/** Where the visitor came from, as the panel's chips read it (app/services/chat_search.py, source_conditions):
 *  a comparison site first, then an ad (a click id, or a paid medium), else the site. */
export function sourceOf(utm: Record<string, string | undefined>): Beat['source'] {
  const source = (utm.utm_source || '').trim().toLowerCase()
  if (COMPARISON.has(source) || utm.kayak_click_id || utm.kclid || utm.skyscanner_click_id) return 'kayak'
  if (utm.gclid || utm.msclkid || utm.fbclid) return 'ads'
  return PAID_MEDIUMS.has((utm.utm_medium || '').trim().toLowerCase()) ? 'ads' : 'site'
}

/** One beat: the path without its query, the trip as codes and dates, the source, the screen. */
export function beatBody(o: {
  path: string; trip: TripPayload | null; utm: Record<string, string | undefined>; phone: boolean; site: string;
  chatOpen: boolean; hasChat: boolean
  /** The page's kind and the visitor's steps, as the site told the widget (siteSignals.ts); bbc-live checks them
   *  again, step by step. */
  pageType?: string | null; activity?: string
  /** The agent-first test: when Alex's card may come (agentTest.ts alexAt), unix seconds. */
  alexAt?: number | null
}): Beat {
  const path = (o.path || '/').split(/[?#]/)[0].slice(0, 120) || '/'
  const t = o.trip
  const code = (v?: string) => (v && /^[A-Z]{3}$/.test(v.toUpperCase()) ? v.toUpperCase() : undefined)
  const day = (v?: string) => (v && /^\d{4}-\d{2}-\d{2}$/.test(v) ? v : undefined)
  const pax = t ? (t.adults || 0) + (t.children || 0) + (t.infants || 0) : 0
  const b: Beat = {
    page: path.startsWith('/') ? path : '/', source: sourceOf(o.utm), device: o.phone ? 'phone' : 'computer',
    site: o.site, chat_open: o.chatOpen, has_chat: o.hasChat,
  }
  if (t) {
    const from = code(t.origin_code), to = code(t.destination_code), depart = day(t.depart_date), ret = day(t.return_date)
    if (from) b.from = from
    if (to) b.to = to
    if (depart) b.depart = depart
    if (ret) b.return = ret
    if (t.cabin && CABINS.has(t.cabin)) b.cabin = t.cabin
    if (pax >= 1 && pax <= 9) b.pax = pax
  }
  if (o.pageType && BEAT_PAGE_TYPES.includes(o.pageType)) b.page_type = o.pageType
  if (o.activity && o.activity.length <= 300) b.activity = o.activity
  if (typeof o.alexAt === 'number' && Number.isInteger(o.alexAt) && o.alexAt > 0) b.alex_at = o.alexAt
  return b
}

/** What a renewal of the pass came to: a new pass (`ok`), the feature or the pass refused for this visitor (`off`),
 *  or nothing known yet (`later`: asked too recently, or the API did not answer). */
export type Renewal = 'ok' | 'off' | 'later'

/** What a renewal's answer means: a pass, off for the page (switched off, refused), or not for now. */
export function renewalOf(cfg: LiveConfig): Renewal {
  if (cfg.later) return 'later'
  return cfg.enabled ? 'ok' : 'off'
}

/** The answer to POST /chat/live that isn't 2xx: the API down or busy (5xx, 429) is for now, anything else off. */
export function failedAsk(status: number): LiveConfig {
  return status >= 500 || status === 429 ? { enabled: false, later: true } : { enabled: false }
}

/** After bbc-live refused the poll's pass: poll again now (a new pass, from this renewal or the beat's), stop for
 *  the page (no pass will come), or wait and try again (a renewal that failed for now must not end the page's
 *  live work: CodeRabbit #273). */
export function afterRefusedPass(passChanged: boolean, renewal: Renewal): 'poll' | 'stop' | 'wait' {
  if (passChanged || renewal === 'ok') return 'poll'
  return renewal === 'off' ? 'stop' : 'wait'
}

/** How long to wait before the next poll after an answer: at once after a 204, longer after errors. */
export function nextPollDelay(status: number | 'error', failures: number): number {
  if (status === 204 || status === 200) return 0
  return Math.min(30_000, 2_000 * 2 ** Math.min(failures, 4))
}
