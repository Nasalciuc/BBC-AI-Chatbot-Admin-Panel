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

/** What the box looks for, as the server reads it (chat_search.parse). `masked`: the reader's rows mask the
 *  client's name and contact (a supervisor), so the server finds those only whole (chat_search, 22 Nov). */
export function searchHint(text: string, masked = false): string {
  const raw = text.trim()
  if (!raw) return ''
  const number = raw.startsWith('#') ? raw.slice(1) : raw
  if (/^[0-9]{1,6}$/.test(number)) return 'The chat number'
  if (raw.includes('@')) return masked ? 'An e-mail, the whole address' : 'An e-mail'
  const digits = raw.replace(/[\s+\-.()/]/g, '')
  if (/^[0-9]{7,}$/.test(digits)) return masked ? 'A phone, the whole number' : 'A phone, by its digits'
  if (/^[A-Z]{3}$/.test(raw)) return 'An airport, departure or arrival'
  return masked ? 'Whole words of a name, or a city in the route' : 'A name, an e-mail, a phone, or a city in the route'
}

/** The longest search the server takes, for the chats and for what was said (GET /api/conversations `search`,
 *  GET /api/search/messages `q`: max_length 100). The box holds no more (CodeRabbit #273: a longer one was sent and
 *  came back refused). */
export const SEARCH_MAX = 100

/** The words of the chats can be searched from three characters on, up to the server's limit. */
export function messagesSearchable(text: string): boolean {
  const t = text.trim()
  return t.length >= 3 && t.length <= SEARCH_MAX
}

/** Who may search what was said (the server's MESSAGE_READERS, app/api/search.py). Supervisors and project
 *  managers don't: their rows mask the contact the client's words carry. */
export const MESSAGE_READERS: readonly string[] = ['owner', 'admin', 'dev', 'qa', 'sales', 'support']
export const MESSAGES_RESTRICTED = "Searching what was said isn't open to your role"

export function canSearchMessages(role: string): boolean {
  return MESSAGE_READERS.includes(role)
}

/** How long the typing pauses before the list and the message search ask the server: one request per word,
 *  not one per keystroke (the message search allows 20 a minute, CodeRabbit #273). */
export const SEARCH_DEBOUNCE_MS = 400

/** The tab a search that found nothing here may still find it in: the chats one tab wider (Mine → All active
 *  or your Closed, All active → Closed), when the role sees that tab; null from the widest. The owner lands on
 *  Mine, where they have no chats, and read "No chat matches" for a chat on All active (secret-site run, 22 Nov). */
export function searchElsewhere(active: string, visible: readonly string[]): string | null {
  const wider: Record<string, readonly string[]> = {
    my_active: ['all_active', 'my_closed'],
    to_read: ['all_active'],
    all_active: ['all_closed'],
  }
  return (wider[active] ?? []).find((k) => visible.includes(k)) ?? null
}

/** What the list says when a search finds no chat on this tab: always the tab it looked in (the list searches
 *  only that tab). With "In what was said" listing messages above, it also says what it looked in, so it no
 *  longer reads as if nothing was found anywhere (CodeRabbit #276). */
export function searchEmptyText(q: string, tabLabel: string, saidAbove: boolean): string {
  return saidAbove
    ? `No chat in ${tabLabel} matches “${q}” by name, contact or route. Where it was said is above.`
    : `No chat in ${tabLabel} matches “${q}”.`
}
