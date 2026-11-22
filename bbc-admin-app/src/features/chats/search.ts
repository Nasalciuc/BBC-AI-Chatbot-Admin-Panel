/**
 * The chats list's search box and filter row (part 4; server: app/services/chat_search.py and the filters of
 * GET /api/conversations). Pure: what the box will look for, and the filters as query parameters.
 */

export interface ListFilters {
  /** New York days, inclusive (YYYY-MM-DD); '' for none. */
  dateFrom: string
  dateTo: string
  contact: 'any' | 'yes' | 'no'
  source: 'any' | 'kayak' | 'ads' | 'site'
  /** Airport codes on the chat's lead. */
  routeFrom: string
  routeTo: string
  crm: 'any' | 'in' | 'out'
}

export const EMPTY_FILTERS: ListFilters = {
  dateFrom: '', dateTo: '', contact: 'any', source: 'any', routeFrom: '', routeTo: '', crm: 'any',
}

const DAY = /^\d{4}-\d{2}-\d{2}$/
const CODE = /^[A-Za-z]{3}$/

export function filterParams(f: ListFilters): Record<string, string> {
  const p: Record<string, string> = {}
  if (DAY.test(f.dateFrom)) p.date_from = f.dateFrom
  if (DAY.test(f.dateTo)) p.date_to = f.dateTo
  if (f.contact !== 'any') p.has_contact = f.contact === 'yes' ? 'true' : 'false'
  if (f.source !== 'any') p.source = f.source
  if (CODE.test(f.routeFrom)) p.route_from = f.routeFrom.toUpperCase()
  if (CODE.test(f.routeTo)) p.route_to = f.routeTo.toUpperCase()
  if (f.crm !== 'any') p.crm = f.crm
  return p
}

/** How many of the row's filters are on (the button's count). */
export function filterCount(f: ListFilters): number {
  const p = filterParams(f)
  return Object.keys(p).length - (p.date_from && p.date_to ? 1 : 0)
}

/** A stable part for the list's query key; '' when no filter is on (the key then stays the heartbeat's). */
export function filterKey(f: ListFilters): string {
  const p = filterParams(f)
  return Object.keys(p).sort().map((k) => `${k}=${p[k]}`).join('&')
}

/** What the box looks for, as the server reads it (chat_search.parse). */
export function searchHint(text: string): string {
  const raw = text.trim()
  if (!raw) return ''
  const number = raw.startsWith('#') ? raw.slice(1) : raw
  if (/^[0-9]{1,6}$/.test(number)) return 'The chat number'
  if (raw.includes('@')) return 'An e-mail'
  const digits = raw.replace(/[\s+\-.()/]/g, '')
  if (/^[0-9]{7,}$/.test(digits)) return 'A phone, by its digits'
  if (/^[A-Z]{3}$/.test(raw)) return 'An airport, departure or arrival'
  return 'A name, an e-mail, a phone, or a city in the route'
}

/** The words of the chats can be searched from three characters on. */
export function messagesSearchable(text: string): boolean {
  return text.trim().length >= 3
}

/** How long the typing pauses before the list and the message search ask the server: one request per word,
 *  not one per keystroke (the message search allows 20 a minute, CodeRabbit #273). */
export const SEARCH_DEBOUNCE_MS = 400
