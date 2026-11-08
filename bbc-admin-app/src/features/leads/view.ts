// What the Leads page says — pure, pinned by tests/leads-view.test.ts.
// The level, "chat with" and CRM state come from the server
// (app/services/leads_view.py), so the rows, tabs and counts agree.
import type { LeadCrmState, LeadLevel, LeadRow, LeadsView, LeadsViewResponse } from '../../lib/types.ts'
import { dayMonth, firstName, gateReasonText } from '../chats/view.ts'

export const LEVELS: Record<LeadLevel, { name: string; chip: string; chipCls: string; why: string; steps: number }> = {
  ready: { name: 'Ready to quote', chip: 'Ready', chipCls: 'bg-k-fdf1d0 text-k-7a5300', why: 'the client confirmed the trip to the assistant', steps: 4 },
  almost: { name: 'Almost ready', chip: 'Almost', chipCls: 'bg-k-f0f0f0 text-k-444444', why: 'route and date, not all of it confirmed', steps: 3 },
  early: { name: 'Early interest', chip: 'Early', chipCls: 'bg-k-f4f4f4 text-k-555555', why: 'a route, no dates yet', steps: 2 },
  contact: { name: 'Contact only', chip: 'Contact', chipCls: 'bg-k-f4f4f4 text-k-6b6b6b', why: 'the form, no trip', steps: 1 },
}

export const TAB_TEXT: Record<LeadsView, string> = {
  not_in_crm:
    'Leads with a phone that the CRM doesn’t have: refused by its check, never sent, or in a chat closed without sending. Closing a chat doesn’t send it.',
  to_review: 'Ready leads nobody has reviewed yet, oldest first. A review is saved on the lead.',
  reviewed: 'Leads someone has reviewed, with the notes saved on the lead.',
  all: 'Every lead the chat collected. The level is what the client told the assistant and confirmed, not how likely they are to buy.',
}

/** The Leads page per role: QA reads and reviews; the owner checks the CRM. */
export function leadsTabsFor(role: string): LeadsView[] {
  return role === 'qa' ? ['to_review', 'reviewed', 'not_in_crm', 'all'] : ['not_in_crm', 'to_review', 'all']
}

export const TAB_LABEL: Record<LeadsView, string> = {
  not_in_crm: 'Not in the CRM',
  to_review: 'To review',
  reviewed: 'Reviewed',
  all: 'All',
}

/** QA's three cards above To review and Reviewed. */
export function reviewCards(r: NonNullable<LeadsViewResponse['review']>): { n: number; text: string }[] {
  return [
    { n: r.to_review, text: 'left to review' },
    { n: r.reviewed_today_by_me, text: 'reviewed by you today' },
    { n: r.ai_only_to_review, text: r.ai_only_to_review === 1 ? 'of them was only with the AI' : 'of them were only with the AI' },
  ]
}

/** "11 of 12 · To review" over the panel's arrows. */
export function navText(index: number, total: number, view: LeadsView): string {
  return `${index + 1} of ${total} · ${TAB_LABEL[view]}`
}

/** Who reviewed it, as the reviewer reads it. */
export function reviewerText(
  r: Pick<LeadRow, 'reviewed_by_qa' | 'reviewed_by' | 'reviewed_at'>,
  meId: string | undefined,
  names: Record<string, string>,
): string {
  if (!r.reviewed_by_qa) return 'Not reviewed yet.'
  const who = r.reviewed_by && r.reviewed_by === meId ? 'you' : r.reviewed_by ? names[r.reviewed_by] : undefined
  const when = r.reviewed_at ? ` on ${dayMonth(r.reviewed_at.slice(0, 10))}` : ''
  return `Reviewed${who ? ` by ${who}` : ''}${when}.`
}

export type Tone = 'ok' | 'ink' | 'warn' | 'bad' | 'muted'

export const TONE_DOT: Record<Tone, string> = {
  ok: 'bg-k-00a645',
  ink: 'bg-kb-202020',
  warn: 'bg-k-d97a00',
  bad: 'bg-k-d93025',
  muted: 'bg-k-9c9c9c',
}

export const TONE_TEXT: Record<Tone, string> = {
  ok: 'text-k-0a6b35',
  ink: 'text-k-202020',
  warn: 'text-k-8a4b00',
  bad: 'text-k-b3261e',
  muted: 'text-k-6b6b6b',
}

const cap = (s: string) => s.replace(/^./, (c) => c.toUpperCase())

/** The CRM column, in one line. */
export function crmLine(r: Pick<LeadRow, 'crm_state' | 'crm_lead_id' | 'crm_push_gate_reason' | 'closed_by_name'>): { text: string; tone: Tone } {
  const state: LeadCrmState = r.crm_state
  switch (state) {
    case 'in_crm':
      return { text: `In the CRM${r.crm_lead_id ? ` · lead ${r.crm_lead_id}` : ''}`, tone: 'ok' }
    case 'refused':
      return { text: `Refused: ${gateReasonText(r.crm_push_gate_reason)}`, tone: 'bad' }
    case 'open':
      return { text: 'Ready to send · chat open', tone: 'ink' }
    case 'closed_by_agent':
      return { text: `Not sent: ${r.closed_by_name ?? 'an agent'} closed the chat`, tone: 'bad' }
    case 'ready_never_sent':
      return { text: 'Ready, never reached the CRM', tone: 'bad' }
    case 'never_sent':
      return { text: 'Never sent', tone: 'bad' }
    case 'no_phone':
      return { text: 'No phone: can’t be sent', tone: 'muted' }
  }
}

/** "6 min", "1 h 10 m", "3 d", then the date. */
/** A lead's age: a duration ("5 min", "3 h 20 m", "2 d"), or "just now", or the day past a week. */
function age(iso: string | null | undefined, nowMs: number): { text: string; duration: boolean } | null {
  if (!iso) return null
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return null
  const min = Math.max(0, Math.floor((nowMs - t) / 60_000))
  if (min < 1) return { text: 'just now', duration: false }
  if (min < 60) return { text: `${min} min`, duration: true }
  const h = Math.floor(min / 60)
  if (h < 24) return { text: `${h} h${min % 60 ? ` ${min % 60} m` : ''}`, duration: true }
  const d = Math.floor(h / 24)
  if (d < 8) return { text: `${d} d`, duration: true }
  return { text: dayMonth(iso.slice(0, 10)), duration: false }
}

/** The Age column: "5 min", "just now", "12 Oct". */
export function ageText(iso: string | null | undefined, nowMs: number): string {
  return age(iso, nowMs)?.text ?? ''
}

/** The same age inside a sentence: "5 min ago", "just now", "12 Oct" (never "just now ago"). */
export function agoText(iso: string | null | undefined, nowMs: number): string {
  const a = age(iso, nowMs)
  if (!a) return ''
  return a.duration ? `${a.text} ago` : a.text
}

function travelers(r: Pick<LeadRow, 'passengers' | 'children_count' | 'infant_count'>): string {
  const n = r.passengers ?? 0
  if (!n) return ''
  const kids = (r.children_count ?? 0) + (r.infant_count ?? 0)
  const adults = Math.max(0, n - kids)
  const parts = [adults === 1 ? '1 adult' : `${adults} adults`]
  if (r.children_count) parts.push(r.children_count === 1 ? '1 child' : `${r.children_count} children`)
  if (r.infant_count) parts.push(r.infant_count === 1 ? '1 infant' : `${r.infant_count} infants`)
  return kids ? parts.join(', ') : n === 1 ? '1 adult' : `${n} adults`
}

const CABIN: Record<string, string> = { business: 'Business', first: 'First', economy: 'Economy', mixed: 'Mixed' }

export function tripLine(r: LeadRow): { route: string; detail: string } {
  const route = r.origin_code && r.destination_code ? `${r.origin_code} → ${r.destination_code}` : r.origin_code || r.destination_code || 'No route'
  const dates = r.departure_date
    ? `${dayMonth(r.departure_date)}${r.return_date ? ` – ${dayMonth(r.return_date)}` : ''}`
    : ''
  const detail = [dates, travelers(r), r.cabin_class ? CABIN[r.cabin_class] ?? r.cabin_class : ''].filter(Boolean).join(' · ')
  return { route, detail }
}

export function personLine(r: Pick<LeadRow, 'visitor_phone' | 'visitor_email'>): string {
  return [r.visitor_phone, r.visitor_email].filter((x) => x && x.trim()).join(' · ')
}

export function chatWithText(r: Pick<LeadRow, 'chat_with' | 'agent_name'>): { text: string; ai: boolean } {
  return r.chat_with === 'ai' ? { text: 'Only the AI', ai: true } : { text: r.agent_name ?? 'An agent', ai: false }
}

/** The cards above "Not in the CRM": why each one isn't there. */
export function summaryCards(s: NonNullable<LeadsViewResponse['summary']>): { n: number; text: string; bad: boolean }[] {
  const out: { n: number; text: string; bad: boolean }[] = []
  if (s.open) out.push({ n: s.open, text: 'still in an open chat', bad: false })
  if (s.closed_by_agent) out.push({ n: s.closed_by_agent, text: 'closed without sending', bad: true })
  if (s.ready_never_sent) out.push({ n: s.ready_never_sent, text: 'ready, never sent', bad: true })
  if (s.never_sent) out.push({ n: s.never_sent, text: 'never sent', bad: true })
  for (const [code, n] of Object.entries(s.refused)) out.push({ n, text: `refused: ${gateReasonText(code)}`, bad: true })
  return out
}

/** The level's checklist in the lead panel. */
export function checklist(r: LeadRow): { label: string; ok: boolean; note?: string }[] {
  const oneWay = r.trip_type === 'one_way'
  return [
    { label: 'Both airports', ok: !!(r.origin_code && r.destination_code) },
    { label: 'A departure date', ok: !!r.departure_date },
    { label: 'How many travel', ok: !!r.passengers },
    { label: 'Return date (or one way)', ok: oneWay || !!r.return_date },
    {
      label: 'A phone the CRM accepts',
      ok: !!r.visitor_phone?.trim() && r.crm_state !== 'refused',
      note: r.crm_state === 'refused' ? gateReasonText(r.crm_push_gate_reason) : undefined,
    },
    { label: 'Confirmed by the client', ok: !!r.confirmed_at, note: r.confirmed_at ? undefined : 'not yet' },
  ]
}

export function missingText(r: LeadRow): string {
  const miss = checklist(r).filter((c) => !c.ok).map((c) => c.label.toLowerCase())
  if (!miss.length || r.level === 'ready') return ''
  return `Missing for Ready: ${miss.join(', ')}.`
}

export interface HistoryItem {
  at: string
  text: string
}

/** What happened to the lead, from the fields the server keeps. Newest first. */
export function historyOf(r: LeadRow, reviewerName?: string | null): HistoryItem[] {
  const out: HistoryItem[] = [{ at: r.created_at, text: `Lead created from chat${r.chat_number ? ` #${r.chat_number}` : ''}` }]
  if (r.confirmed_at) out.push({ at: r.confirmed_at, text: `${cap(firstName(r.visitor_name))} confirmed the trip to the assistant` })
  if (r.conversation_closed_at) {
    out.push({ at: r.conversation_closed_at, text: r.closed_by_name ? `${r.closed_by_name} closed the chat` : 'The chat closed' })
  }
  if (r.created_in_crm && r.created_in_crm_at) {
    out.push({ at: r.created_in_crm_at, text: `Sent to the CRM${r.crm_lead_id ? ` · lead ${r.crm_lead_id}` : ''}` })
  }
  if (r.reviewed_by_qa && r.reviewed_at) out.push({ at: r.reviewed_at, text: `Reviewed${reviewerName ? ` by ${reviewerName}` : ''}` })
  return out.sort((a, b) => b.at.localeCompare(a.at))
}

function csvCell(v: unknown): string {
  const s = v === null || v === undefined ? '' : String(v)
  // Neutralise spreadsheet formulas; quote everything.
  const safe = /^[=+\-@]/.test(s) ? `'${s}` : s
  return `"${safe.replace(/"/g, '""')}"`
}

export function leadsCsv(rows: LeadRow[]): string {
  const head = ['Name', 'Phone', 'E-mail', 'Route', 'Departure', 'Return', 'Travelers', 'Level', 'Chat with', 'CRM', 'Reviewed', 'Chat #', 'Created']
  const lines = rows.map((r) =>
    [
      r.visitor_name, r.visitor_phone, r.visitor_email, tripLine(r).route, r.departure_date, r.return_date,
      r.passengers, LEVELS[r.level].name, chatWithText(r).text, crmLine(r).text, r.reviewed_by_qa ? 'yes' : 'no',
      r.chat_number, r.created_at,
    ].map(csvCell).join(',')
  )
  return [head.map(csvCell).join(','), ...lines].join('\n')
}
