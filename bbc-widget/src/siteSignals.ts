/**
 * What the site tells the chat (bbc-signals.astro, on buybusinessclass.com and businessclass-tickets.com):
 *
 *   window.bbcPage    what the page is about: { site, page_type, trip? }, written when the page is drawn
 *   window.bbcTrack   the site calls it when the visitor does something; each event waits in
 *                     window.bbcQueue (the last 50, for a widget that loads late) and goes out as a
 *                     `bbc:<name>` CustomEvent on window
 *
 * The widget reads the queue once, then listens. Everything is checked here as an address would be: a
 * value of the wrong shape is dropped, never trusted, and nothing personal is kept — the site sends field
 * names, codes and dates, never what the visitor typed, and only those survive the checks.
 *
 * The site sends its own events to its own PostHog (bbc-signals.astro); the widget never sends them again.
 *
 * Pure functions on top (node-testable, no imports); the page's store at the bottom.
 */

export const SITE_EVENTS = [
  'search_submitted',
  'quote_form_opened',
  'quote_form_step',
  'quote_form_invalid',
  'quote_form_submitting',
  'quote_form_failed',
  'quote_form_submitted',
  'contact_clicked',
] as const
export type SiteEventName = typeof SITE_EVENTS[number]

export const PAGE_TYPES = [
  'home', 'route', 'destination', 'origin', 'airline', 'deals', 'search',
  'flight', 'quote', 'thank_you', 'blog', 'about', 'legal', 'other',
] as const
export type PageType = typeof PAGE_TYPES[number]

export interface SitePage {
  site: 'bbc' | 'bct' | null
  pageType: PageType | null
  /** The fields as the site gave them; tripContext.tripFromFields holds them to the address's rules. */
  trip: Record<string, unknown> | null
}

export interface SiteEvent {
  name: SiteEventName
  detail: Record<string, unknown>
  /** When it happened (the site's Date.now()), never later than when the widget heard it. */
  at: number
}

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)

/** window.bbcPage, or null when it says nothing the widget can use. */
export function readSitePage(raw: unknown): SitePage | null {
  if (!isObj(raw)) return null
  const site = raw.site === 'bbc' || raw.site === 'bct' ? raw.site : null
  const pageType = typeof raw.page_type === 'string' && (PAGE_TYPES as readonly string[]).includes(raw.page_type)
    ? raw.page_type as PageType
    : null
  const trip = isObj(raw.trip) ? raw.trip : null
  if (!site && !pageType && !trip) return null
  return { site, pageType, trip }
}

/** Nothing opens by itself on a page the site calls a thank-you or legal page: the request was just
 *  sent, or the visitor is reading terms. The same rule as triggers.ts's paths, for addresses it misses. */
export function noMomentPage(page: SitePage | null): boolean {
  return page?.pageType === 'thank_you' || page?.pageType === 'legal'
}

/** One entry of window.bbcQueue or one bbc:* event, or null when it is none the widget knows. A time that
 *  is missing, not a number or in the future (a clock the site got wrong) reads as `now`. The detail is
 *  rebuilt from the fields that event may carry (cleanDetail): nothing else the site put in it is kept. */
export function readSiteEvent(name: unknown, detail: unknown, at: unknown, now: number): SiteEvent | null {
  if (typeof name !== 'string' || !(SITE_EVENTS as readonly string[]).includes(name)) return null
  const t = typeof at === 'number' && Number.isFinite(at) && at > 0 && at <= now ? at : now
  return { name: name as SiteEventName, detail: cleanDetail(name as SiteEventName, isObj(detail) ? detail : {}), at: t }
}

// The few shapes an event's detail may carry into the activity line. Codes and dates follow
// tripContext.ts (an IATA code, YYYY-MM-DD).
const CODE = /^[A-Za-z]{3}$/
const DATE = /^\d{4}-\d{2}-\d{2}$/
const CHANNELS = ['phone', 'whatsapp', 'email'] as const
const REASONS = ['server', 'network', 'captcha', 'other'] as const

const code = (v: unknown) => (typeof v === 'string' && CODE.test(v.trim()) ? v.trim().toUpperCase() : null)
const date = (v: unknown) => (typeof v === 'string' && DATE.test(v.trim()) ? v.trim() : null)

/** What a flagged field is, in a fixed list of words. The site sends field names; whatever it sends
 *  (a name, or by mistake what the visitor typed), only a word of this list goes further. */
export const FIELD_KINDS = [
  'name', 'email', 'phone', 'origin', 'destination', 'dates', 'passengers', 'cabin', 'trip_type', 'message',
  'consent', 'captcha', 'other',
] as const
export type FieldKind = typeof FIELD_KINDS[number]

// Read on the name in lower case without separators ('phone_number' → 'phonenumber'), first rule first.
const KIND_RULES: ReadonlyArray<readonly [RegExp, FieldKind]> = [
  [/captcha|turnstile/, 'captcha'],
  [/mail/, 'email'],
  [/phone|mobile|^tel|whatsapp/, 'phone'],
  [/consent|terms|agree|privacy|gdpr|newsletter/, 'consent'],
  [/^from$|origin|leaving|departure(?:city|airport)|from(?:city|airport)/, 'origin'],
  [/^to$|destination|^dest|going|arrival(?:city|airport)|to(?:city|airport)$/, 'destination'],
  [/date|when|depart|return/, 'dates'],
  [/passenger|^pax|adult|child|infant|travel+er|people|guests?$/, 'passengers'],
  [/cabin|class/, 'cabin'],
  [/trip|oneway|roundtrip|multicity/, 'trip_type'],
  [/name/, 'name'],
  [/message|comment|note|request|details|question/, 'message'],
]

/** The kind of one flagged field; null for nothing a name could be (empty, digits, not a string). */
export function fieldKind(v: unknown): FieldKind | null {
  if (typeof v !== 'string') return null
  const n = v.toLowerCase().replace(/[^a-z]/g, '')
  if (!n) return null
  for (const [re, kind] of KIND_RULES) if (re.test(n)) return kind
  return 'other'
}

// The autocomplete tokens of a person's contact (HTML spec), read as whole tokens: the substring rules
// above would take `country-name` for a name (CodeRabbit #268, round 3).
const AUTOCOMPLETE_KINDS: Readonly<Record<string, FieldKind>> = {
  name: 'name', 'given-name': 'name', 'additional-name': 'name', 'family-name': 'name', nickname: 'name',
  'honorific-prefix': 'name', 'honorific-suffix': 'name',
  email: 'email',
  tel: 'phone', 'tel-country-code': 'phone', 'tel-national': 'phone', 'tel-area-code': 'phone', 'tel-local': 'phone',
  'tel-local-prefix': 'phone', 'tel-local-suffix': 'phone', 'tel-extension': 'phone',
}

/** The contact kind an autocomplete value names: its last token (after a section-* or shipping/billing
 *  word), when it is one of the contact tokens; null for anything else ("off", "on", `country-name`). */
export function autocompleteKind(v: unknown): FieldKind | null {
  if (typeof v !== 'string') return null
  const token = v.trim().toLowerCase().split(/\s+/).pop() ?? ''
  return AUTOCOMPLETE_KINDS[token] ?? null
}

/** The kind of a field from what the page calls it: a contact token of its autocomplete, else the first of
 *  its other names (its name, then its id) that names a kind of the list; 'other' only when none does.
 *  autocomplete="off", "on" or a token of no contact names nothing here, so the name speaks instead.
 *  Never what was typed in it. */
export function kindOfField(autocomplete?: unknown, ...names: unknown[]): FieldKind | null {
  const token = autocompleteKind(autocomplete)
  if (token) return token
  let other: FieldKind | null = null
  for (const s of names) {
    const k = fieldKind(s)
    if (k && k !== 'other') return k
    other = other ?? k
  }
  return other
}

/** `kinds`, then the ones of `more` not in it yet: each kind once, 6 at most (one refused send's list). */
export function withKinds(kinds: readonly FieldKind[], more: readonly (FieldKind | null)[]): FieldKind[] {
  const out = [...kinds]
  for (const k of more) if (k && !out.includes(k) && out.length < 6) out.push(k)
  return out
}

/** The kinds of the fields a refused send flagged: up to 6, each once, in the site's order. */
export function fieldKinds(v: unknown): FieldKind[] {
  if (!Array.isArray(v)) return []
  const out: FieldKind[] = []
  for (const f of v) {
    const k = fieldKind(f)
    if (k && !out.includes(k)) out.push(k)
    if (out.length === 6) break
  }
  return out
}

export function contactChannel(v: unknown): typeof CHANNELS[number] | null {
  return typeof v === 'string' && (CHANNELS as readonly string[]).includes(v) ? v as typeof CHANNELS[number] : null
}

function failReason(v: unknown): typeof REASONS[number] {
  return typeof v === 'string' && (REASONS as readonly string[]).includes(v) ? v as typeof REASONS[number] : 'other'
}

// A place as a search form names it ("Chicago (CHI)", "St. Louis", "São Paulo"): letters and the few marks
// a city name has, 2–40 characters. An email, a phone number or a sentence is no place.
const PLACE = /^\p{L}[\p{L} .'’()-]{0,38}[\p{L}.)]$/u
const WORD = /^[A-Za-z_ -]{2,20}$/
const FORMS = ['main', 'popup'] as const

const place = (v: unknown) => (typeof v === 'string' && PLACE.test(v.trim()) ? v.trim() : null)
const word = (v: unknown) => (typeof v === 'string' && WORD.test(v.trim()) ? v.trim().toLowerCase() : null)
/** A number of passengers (0–9), as a number or a string of digits. */
const count = (v: unknown) => {
  const n = typeof v === 'number' ? v : typeof v === 'string' && /^\d{1,2}$/.test(v.trim()) ? Number(v.trim()) : NaN
  return Number.isInteger(n) && n >= 0 && n <= 9 ? n : null
}
/** The step a multi-step form moved to (1–20). */
const step = (v: unknown) => (typeof v === 'number' && Number.isInteger(v) && v > 0 && v < 21 ? v : null)

/** The detail an event may carry, rebuilt from the fields of its kind that pass their check; anything
 *  else the site put in it (a name, an email, a message) is dropped on arrival and never kept here. */
function cleanDetail(name: SiteEventName, d: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  const put = (k: string, v: unknown) => { if (v !== null) out[k] = v }
  switch (name) {
    case 'search_submitted':
      put('origin_code', code(d.origin_code))
      put('destination_code', code(d.destination_code))
      put('origin_name', place(d.origin_name))
      put('destination_name', place(d.destination_name))
      put('depart_date', date(d.depart_date))
      put('return_date', date(d.return_date))
      put('trip_type', word(d.trip_type))
      put('cabin', word(d.cabin))
      put('adults', count(d.adults))
      put('children', count(d.children))
      put('infants', count(d.infants))
      break
    case 'quote_form_opened':
      put('form', typeof d.form === 'string' && (FORMS as readonly string[]).includes(d.form) ? d.form : null)
      break
    case 'quote_form_step':
      put('step', step(d.step))
      break
    case 'quote_form_invalid':
      out.fields = fieldKinds(d.fields)
      break
    case 'quote_form_failed':
      out.reason = failReason(d.reason)
      break
    case 'contact_clicked':
      put('channel', contactChannel(d.channel))
      break
    default:
      break
  }
  return out
}

/** One event as a short token for the server's line about the visit (metadata.page_activity): what the
 *  visitor did, never what they typed. */
export function activityToken(e: SiteEvent): string {
  const d = e.detail
  switch (e.name) {
    case 'search_submitted': {
      const o = code(d.origin_code)
      const to = code(d.destination_code)
      const when = date(d.depart_date)
      return 'search' + (o || to ? `:${o ?? '?'}-${to ?? '?'}` : '') + (when ? `:${when}` : '')
    }
    case 'quote_form_opened': return 'quote_opened'
    case 'quote_form_step': {
      const n = step(d.step)
      return n ? `quote_step:${n}` : 'quote_step'
    }
    case 'quote_form_invalid': {
      const f = fieldKinds(d.fields)
      return f.length ? `quote_invalid:${f.join('|')}` : 'quote_invalid'
    }
    case 'quote_form_submitting': return 'quote_submitting'
    case 'quote_form_failed': return `quote_failed:${failReason(d.reason)}`
    case 'quote_form_submitted': return 'quote_submitted'
    case 'contact_clicked': {
      const c = contactChannel(d.channel)
      return c ? `contact:${c}` : 'contact'
    }
  }
}

/** What the visitor did on the site before the chat, oldest first, for the server: the last `max` steps,
 *  a step repeated back to back said once, at most 300 characters. Empty when the site said nothing. */
export function activityLine(events: readonly SiteEvent[], max = 12): string {
  const tokens: string[] = []
  for (const e of events) {
    const t = activityToken(e)
    if (tokens[tokens.length - 1] !== t) tokens.push(t)
  }
  let line = tokens.slice(-max).join(';')
  while (line.length > 300 && line.includes(';')) line = line.slice(line.indexOf(';') + 1)
  return line.length > 300 ? '' : line
}

/** What a refused, failed or started send of the quote form means for the quote moment (triggers.ts).
 *  The site's word replaces guessing from the page's HTML: a press, a flagged field, a send in flight. */
export interface QuotePress {
  /** When the form was first pressed (or refused, or sent) in this watch. */
  at: number
  /** The last time the visitor worked on the form. */
  activityAt: number
  /** A field was flagged, or the send failed: the visitor is stuck, the moment comes sooner. */
  invalid: boolean
  /** A send is in flight: nothing interrupts it. */
  busy: boolean
}

export interface SiteEffect {
  press: QuotePress | null
  /** The visitor sent the quote request or reached out by phone, WhatsApp or email: no moment this visit. */
  stop: 'sent' | 'contact' | null
}

export function applySiteEvent(press: QuotePress | null, e: SiteEvent): SiteEffect {
  const base = (): QuotePress => press ?? { at: e.at, activityAt: e.at, invalid: false, busy: false }
  switch (e.name) {
    case 'quote_form_opened':
    case 'quote_form_step':
      // Working on the form is no press; it only says the form is not quiet.
      return { press: press ? { ...press, activityAt: Math.max(press.activityAt, e.at) } : null, stop: null }
    case 'quote_form_invalid':
      return { press: { ...base(), activityAt: e.at, invalid: true, busy: false }, stop: null }
    case 'quote_form_submitting':
      return { press: { ...base(), activityAt: e.at, busy: true }, stop: null }
    case 'quote_form_failed':
      return { press: { ...base(), activityAt: e.at, invalid: true, busy: false }, stop: null }
    case 'quote_form_submitted':
      return { press: null, stop: 'sent' }
    case 'contact_clicked':
      return { press, stop: 'contact' }
    default:
      return { press, stop: null }
  }
}

/** The site's word that the visitor is working in its quote form, pressed or not: they opened it, moved a
 *  step, a field was refused, a send went out or failed. No card comes up over it (triggers.ts), nor the
 *  10 s note beside the button (Widget.tsx). A sent form is `quote_form_submitted`: a stop, not work. */
export const QUOTE_FORM_WORK: readonly SiteEventName[] = [
  'quote_form_opened', 'quote_form_step', 'quote_form_invalid', 'quote_form_submitting', 'quote_form_failed',
]

export function isQuoteFormWork(e: SiteEvent): boolean {
  return QUOTE_FORM_WORK.includes(e.name)
}

// ── The page's store ────────────────────────────────────────────────────────────────────────────────────

type Listener = (e: SiteEvent) => void

let events: SiteEvent[] = []
const listeners = new Set<Listener>()
let detach: (() => void) | null = null
let pageSource: () => unknown = () => undefined

/** Reads what the site queued before the widget loaded, then listens for the rest. Once per page; a
 *  second call (a remount) starts over. */
export function startSiteSignals(w: Window): void {
  stopSiteSignals()
  const win = w as Window & { bbcPage?: unknown; bbcQueue?: unknown }
  pageSource = () => win.bbcPage
  const now = Date.now()
  events = []
  try {
    const q = win.bbcQueue
    if (Array.isArray(q)) {
      for (const item of q.slice(-50)) {
        const e = isObj(item) ? readSiteEvent(item.name, item.detail, item.at, now) : null
        if (e) events.push(e)
      }
    }
  } catch { /* a queue the widget cannot read is no queue */ }
  const handlers: Array<[string, EventListener]> = SITE_EVENTS.map(name => [`bbc:${name}`, (ev: Event) => {
    const t = Date.now()
    const e = readSiteEvent(name, (ev as CustomEvent).detail, t, t)
    if (!e) return
    events.push(e)
    if (events.length > 50) events.shift()
    for (const l of [...listeners]) {
      try { l(e) } catch { /* one listener's error never stops the others */ }
    }
  }])
  for (const [type, h] of handlers) {
    try { win.addEventListener(type, h) } catch { /* no events here: the queue alone */ }
  }
  detach = () => {
    for (const [type, h] of handlers) {
      try { win.removeEventListener(type, h) } catch { /* already gone */ }
    }
  }
}

/** Stops listening and forgets the page (the widget's teardown): nothing the site said stays here. */
export function stopSiteSignals(): void {
  detach?.()
  detach = null
  listeners.clear()
  events = []
  pageSource = () => undefined
}

/** What the page says it is about (read each time: a site may set it after the widget loaded). */
export function sitePage(): SitePage | null {
  try { return readSitePage(pageSource()) } catch { return null }
}

/** Everything the site said so far on this page, oldest first. */
export function siteEvents(): readonly SiteEvent[] {
  return events
}

/** The site's events from now on; returns the way to stop listening. */
export function onSiteEvent(l: Listener): () => void {
  listeners.add(l)
  return () => { listeners.delete(l) }
}

/** The detail of the visitor's last search on the site, or null. */
export function lastSearch(): Record<string, unknown> | null {
  for (let i = events.length - 1; i >= 0; i--) if (events[i].name === 'search_submitted') return events[i].detail
  return null
}

/** What the conversation carries about the page (metadata; strings only, nothing personal). The server
 *  keeps only the keys it knows. */
export function siteMetadata(): Record<string, string> {
  const out: Record<string, string> = {}
  const page = sitePage()
  if (page?.pageType) out.page_type = page.pageType
  const line = activityLine(events)
  if (line) out.page_activity = line
  return out
}
