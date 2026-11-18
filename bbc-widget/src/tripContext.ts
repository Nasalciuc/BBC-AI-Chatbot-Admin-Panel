/**
 * The trip the visitor is looking at, read from the page, so the chat that opens by itself starts from
 * their own search instead of asking for it again. The server composes the words and checks every field
 * on its own (bbc-chatbot-api app/models/trip_context.py): this only keeps out what is plainly not a
 * trip (long strings, wrong shapes) and never sends free text it cannot place.
 *
 * The shapes buybusinessclass.com uses (PostHog, 12–15 Nov 2026):
 *   Kayak landing  /business-class-flights?from=ATL&to=ICN&depart_at=2027-05-25&return_at=2027-06-28
 *                  &adults=4&childrens=0&infants=0&cabin_class=first&trip_type=round_trip&utm_source=kayak
 *   flight page    /flight?fly_from=…&fly_to=…&depart_at=…   (otherwise the same keys)
 *   site search    /search?tripType=round_trip&from-0=Chicago (CHI)&from-0-code=CHI&to-0=…&to-0-code=…
 *                  &date-0=…&returnDate=…&cabin=business&adults=1&children=…&infants=…
 * and route pages that name the trip only in their title (the same h1 shapes as pageContext.ts):
 *   "New York to London business class flights", "Business class flights to Abu Dhabi", …
 *
 * Pure: the address and the title come in as strings (node-testable).
 */

export type TripSource = 'kayak' | 'search' | 'route_page'

/** The body's `trip` for /chat/proactive/offer and /chat/init — the server's TripContext, snake_case. */
export interface TripPayload {
  source: TripSource
  origin_code?: string
  destination_code?: string
  origin_name?: string
  destination_name?: string
  depart_date?: string
  return_date?: string
  trip_type?: string
  cabin?: string
  adults?: number
  children?: number
  infants?: number
}

const CODE = /^[A-Za-z]{3}$/
const DATE = /^\d{4}-\d{2}-\d{2}$/
const COUNT = /^\d{1,2}$/
const WORD = /^[A-Za-z_ -]{2,20}$/

const code = (v: string | null) => (v && CODE.test(v.trim()) ? v.trim().toUpperCase() : undefined)
const day = (v: string | null) => (v && DATE.test(v.trim()) ? v.trim() : undefined)
const count = (v: string | null) => (v && COUNT.test(v.trim()) ? Number(v.trim()) : undefined)
const word = (v: string | null) => (v && WORD.test(v.trim()) ? v.trim().toLowerCase() : undefined)

/** The server's rule for a place name (app/models/trip_context.py, `_name`): a letter of any script first, then
 *  letters, spaces and . ' - ( ) , only. A name it would drop is dropped here too, so the card never counts on a
 *  destination the server's words will not say ("Paris / France": CodeRabbit #269). */
const NAME = /^\p{L}[\p{L} .'(),-]*$/u
/** The server's whitespace (`_name`: " ".join(v.split()), Python's str.isspace). JS's \s leaves out U+0085 and
 *  U+001C–U+001F and takes in U+FEFF: folding by it, "São\u0085Paulo" was dropped here and kept there, and a U+FEFF
 *  went the other way. Folded by the server's set, a U+FEFF stays and fails the name rule on both (CodeRabbit #269). */
const SERVER_SPACE = /[\t\n\v\f\r\x1c-\x1f \x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+/g

/** A city as the page wrote it: "Chicago (CHI)" → "Chicago". 2–40 characters, by the server's rule, else nothing.
 *  The server's spaces fold first, so the airport code still comes off when a NEL follows it ("Paris (CDG)" then
 *  U+0085), and the characters count as Python's len() counts them, in code points: a letter outside the Basic
 *  Multilingual Plane is one character there and two UTF-16 units here (CodeRabbit #269). */
function city(v: string | null | undefined): string | undefined {
  if (!v) return undefined
  const s = v.replace(SERVER_SPACE, ' ').replace(/^ | $/g, '').replace(/ ?\([A-Za-z]{3}\)$/, '')
  const n = Array.from(s).length
  return n >= 2 && n <= 40 && NAME.test(s) ? s : undefined
}

/** The first of `keys` whose value reads (a page uses one spelling of each field; an unrelated
 *  `from=newsletter` must not hide the search's own `from-0-code`). */
function pick<T>(p: URLSearchParams, read: (v: string | null) => T | undefined, ...keys: string[]): T | undefined {
  for (const k of keys) {
    const v = read(p.get(k))
    if (v !== undefined) return v
  }
  return undefined
}

function compact(t: TripPayload): TripPayload {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(t)) if (v !== undefined) out[k] = v
  return out as unknown as TripPayload
}

/** A trip carried by the address (Kayak, /flight, /search), or null. `kayakVisit`: the visit came from
 *  Kayak (the tab's first touch), for a page of that visit whose own address carries no utm_source. */
export function tripFromUrl(search: string, kayakVisit = false): TripPayload | null {
  let p: URLSearchParams
  try { p = new URLSearchParams(search) } catch { return null }
  const kayak = kayakVisit || (p.get('utm_source') || '').toLowerCase() === 'kayak'
  const t: TripPayload = {
    source: kayak ? 'kayak' : 'search',
    origin_code: pick(p, code, 'from', 'fly_from', 'from-0-code'),
    destination_code: pick(p, code, 'to', 'fly_to', 'to-0-code'),
    origin_name: pick(p, city, 'from-0'),
    destination_name: pick(p, city, 'to-0'),
    depart_date: pick(p, day, 'depart_at', 'date-0'),
    return_date: pick(p, day, 'return_at', 'returnDate'),
    trip_type: pick(p, word, 'trip_type', 'tripType'),
    cabin: pick(p, word, 'cabin_class', 'cabin'),
    adults: pick(p, count, 'adults'),
    children: pick(p, count, 'childrens', 'children'),
    infants: pick(p, count, 'infants'),
  }
  const hasTrip = t.origin_code || t.destination_code || t.destination_name || t.depart_date
  return hasTrip ? compact(t) : null
}

/** A route page's title, or null. Only the shapes that name a place (an airline page names none). */
export function tripFromTitle(h1: string): TripPayload | null {
  const s = (h1 || '').replace(/\s+/g, ' ').trim()
  if (!s || s.length > 90) return null
  let m: RegExpMatchArray | null
  if ((m = s.match(/^(.+?) to (.+?) (business|first) class flights$/i))) {
    return compact({ source: 'route_page', origin_name: city(m[1]), destination_name: city(m[2]), cabin: m[3].toLowerCase() })
  }
  if ((m = s.match(/^(business|first) class flights to (.+)$/i))) {
    return compact({ source: 'route_page', destination_name: city(m[2]), cabin: m[1].toLowerCase() })
  }
  if ((m = s.match(/^(business|first) class flights from (.+)$/i))) {
    return compact({ source: 'route_page', origin_name: city(m[2]), cabin: m[1].toLowerCase() })
  }
  return null
}

/** A trip the site handed over as fields (bbc-signals.astro: `window.bbcPage.trip`, or the detail of a
 *  `search_submitted` event), held to the same rules as the address. Numbers or strings; anything else,
 *  and any field of the wrong shape, is left out. Null when nothing places a trip. */
export function tripFromFields(raw: unknown, source: TripSource): TripPayload | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const r = raw as Record<string, unknown>
  const s = (v: unknown): string | null =>
    typeof v === 'string' ? v : typeof v === 'number' && Number.isFinite(v) ? String(v) : null
  const t: TripPayload = {
    source,
    origin_code: code(s(r.origin_code)),
    destination_code: code(s(r.destination_code)),
    origin_name: city(s(r.origin_name)),
    destination_name: city(s(r.destination_name)),
    depart_date: day(s(r.depart_date)),
    return_date: day(s(r.return_date)),
    trip_type: word(s(r.trip_type)),
    cabin: word(s(r.cabin)),
    adults: count(s(r.adults)),
    children: count(s(r.children)),
    infants: count(s(r.infants)),
  }
  const hasTrip = t.origin_code || t.destination_code || t.destination_name || t.depart_date
  return hasTrip ? compact(t) : null
}

/** The trip the chat starts from, the newest word first: the search the visitor just ran on the site, the
 *  address they came in with, what the page itself says it is about (bbc-signals), and last its title. */
export function pickTrip(...trips: (TripPayload | null | undefined)[]): TripPayload | null {
  return trips.find((t): t is TripPayload => !!t) ?? null
}

/** The card can name a place: the server opens with the trip, not with the general question. A destination
 *  code equal to the origin's is none: the server drops it (app/models/trip_context.py, `_consistent`), and the
 *  card would ask where to after all (CodeRabbit #269). */
export function hasContext(trip: TripPayload | null): boolean {
  if (!trip) return false
  if (trip.destination_name) return true
  return !!trip.destination_code && trip.destination_code !== trip.origin_code
}
