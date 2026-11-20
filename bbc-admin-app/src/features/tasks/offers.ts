// Offers to send (ADR-6b, ADR-8b) — every sentence the queue shows, pure (tests/offers-view.test.ts).
// The rows, their order and who sees what come from the server (app/services/offers_view.py).

import type { OfferChannel, OfferDone, OfferRefusal, OfferRow, OffersView } from '../../lib/types.ts'
import { dayMonth, firstName, travelersText } from '../chats/view.ts'
import { crmLine, type Tone } from '../leads/view.ts'
import { clockIn, dayIn, span } from './view.ts'

export const WINDOWS = [1, 3, 7, 14] as const
export type OfferWindow = (typeof WINDOWS)[number]
export const DEFAULT_WINDOW: OfferWindow = 3

export const WINDOW_LABEL: Record<OfferWindow, string> = {
  1: 'Last 24 hours',
  3: 'Last 3 days',
  7: 'Last 7 days',
  14: 'Last 14 days',
}

export function windowOf(value: unknown): OfferWindow {
  const n = Number(value)
  return (WINDOWS as readonly number[]).includes(n) ? (n as OfferWindow) : DEFAULT_WINDOW
}

// "text" is an SMS: the chat's SMS button and a client who typed "text me" (20 Nov: SMS in place of
// WhatsApp; the agents text from the company's number). WhatsApp stays for older leads and clients who name it.
const ASKED: Record<OfferChannel, string> = {
  whatsapp: 'WhatsApp',
  text: 'an SMS',
  call: 'a call back',
  email: 'e-mail',
}

function orList(items: string[]): string {
  if (items.length <= 1) return items[0] ?? ''
  return `${items.slice(0, -1).join(', ')} or ${items[items.length - 1]}`
}

/** How to send it: the channel the client asked for, else what their contact allows and their refusals
 *  leave. `kind` picks the icon. */
export function channelLine(
  r: Pick<OfferRow, 'channels' | 'email' | 'phone' | 'phone_to_check' | 'blind' | 'refused'>,
): { text: string; kind: OfferChannel | 'any' } {
  if (r.channels.length) return { text: `Asked for ${orList(r.channels.map((c) => ASKED[c]))}`, kind: r.channels[0] }
  if (r.blind) return { text: 'No channel asked yet', kind: 'any' }
  // A phone the client said not to call or text is no way to send it (CodeRabbit #270).
  const refused = new Set<OfferRefusal>(r.refused ?? [])
  const hasPhone = !!(r.phone || r.phone_to_check)
  const call = hasPhone && !refused.has('no calls')
  const text = hasPhone && !refused.has('no texts')
  const email = !!r.email && !refused.has('no email')
  const byPhone = call && text ? 'phone' : call ? 'a call' : text ? 'an SMS' : null
  if (byPhone && email) return { text: `No channel asked: ${byPhone} or e-mail`, kind: 'any' }
  if (email) return { text: hasPhone ? 'E-mail only: no calls or texts' : 'Only an e-mail: send it there', kind: 'email' }
  if (call && text) return { text: 'Only a phone: call or text', kind: 'any' }
  if (text) return { text: 'Only a phone: text it, no calls', kind: 'text' }
  if (call) return { text: 'Only a phone: call, no texts', kind: 'call' }
  return { text: 'Every channel turned down: ask the client how to send it', kind: 'any' }
}

/** "No calls · No texts": the channels the client turned down. */
export function refusedLine(refused: OfferRefusal[]): string {
  return refused.map((x) => x.charAt(0).toUpperCase() + x.slice(1)).join(' · ')
}

/** "Waiting 5 min", "Waiting 3 h 20 min", "Waiting 2 d"; "Just now" under a minute. */
export function waitingText(iso: string, nowMs: number): string {
  const ms = nowMs - Date.parse(iso)
  if (!(ms >= 60_000)) return 'Just now'
  return `Waiting ${span(ms)}`
}

/** Who the row is about: the name, else the chat's number. */
export function clientName(r: Pick<OfferRow, 'client' | 'chat_number'>): string {
  return r.client || (r.chat_number != null ? `Chat #${r.chat_number}` : 'A client')
}

const cabin = (c: string | null) => (c ? (c.charAt(0).toUpperCase() + c.slice(1)).replace(/_/g, ' ') : '')

/** "NYC → LON · 10 Dec – 17 Dec · 2 travelers · Business"; a blind row says how to see it. */
export function tripLine(
  r: Pick<OfferRow, 'route' | 'departure_date' | 'return_date' | 'passengers' | 'children_count' | 'infant_count' | 'cabin_class' | 'blind'>,
): string {
  if (r.blind) return 'Waiting for a person in the chats: take it there to see the trip and the contact.'
  const dep = dayMonth(r.departure_date)
  const ret = dayMonth(r.return_date)
  const dates = dep && ret ? `${dep} – ${ret}` : dep
  return [r.route, dates, travelersText(r), cabin(r.cabin_class)].filter(Boolean).join(' · ')
}

/** Who talked to the client, and whether the chat is still open. */
export function whoLine(r: Pick<OfferRow, 'agent_name' | 'chat_status'>): string {
  const who = r.agent_name ? `With ${firstName(r.agent_name)}` : 'Only with the assistant'
  return r.chat_status && r.chat_status !== 'closed' ? `${who} · chat still open` : who
}

/** The CRM in one line (the Leads page's words); null on a blind row. */
export function crmNote(r: Pick<OfferRow, 'crm_state' | 'crm_lead_id' | 'crm_gate' | 'agent_name'>): { text: string; tone: Tone } | null {
  if (!r.crm_state) return null
  return crmLine({ crm_state: r.crm_state, crm_lead_id: r.crm_lead_id, crm_push_gate_reason: r.crm_gate, closed_by_name: r.agent_name })
}

/** "2 chats": the client came back; the mark goes on every one of them. */
export function chatsNote(r: Pick<OfferRow, 'chats'>): string {
  return r.chats.length > 1 ? `${r.chats.length} chats` : ''
}

/** The other chats a mark covers (the row's own goes first, in the URL): every one of them, or part of
 *  the client stays unmarked (CodeRabbit #270). */
export function otherChats(r: Pick<OfferRow, 'conversation_id' | 'chats'>): string[] {
  return r.chats.map((c) => c.conversation_id).filter((id) => id && id !== r.conversation_id)
}

/** Why a reader who may not mark sees the marks locked (QA reads the queue, the sales team marks it). */
export const MARK_RESTRICTED = 'Only the sales team marks offers'

/** "Sent · Agent One · 10:42" (today) or "Not needed · Agent One · Tue 17 Nov, 09:05". */
export function doneLine(d: Pick<OfferDone, 'state' | 'by_name' | 'at'>, nowMs: number, tz: string): string {
  const at = Date.parse(d.at)
  const when = dayIn(at, tz) === dayIn(nowMs, tz) ? clockIn(at, tz) : `${dayIn(at, tz)}, ${clockIn(at, tz)}`
  return [d.state === 'sent' ? 'Sent' : 'Not needed', d.by_name, when].filter(Boolean).join(' · ')
}

/** "4 waiting · 2 sent · 1 not needed". */
export function summaryText(counts: OffersView['counts']): string {
  const bits = [`${counts.waiting} waiting`, `${counts.sent} sent`]
  if (counts.not_needed) bits.push(`${counts.not_needed} not needed`)
  return bits.join(' · ')
}

export function emptyText(days: OfferWindow): string {
  const span = days === 1 ? 'the last 24 hours' : `the last ${days} days`
  return `Nothing waiting. Every client with a contact and a trip from ${span} has an offer or was marked.`
}

/** The toast after a mark, with its Undo. */
export function markedText(state: 'sent' | 'not_needed' | 'open', name: string): string {
  if (state === 'sent') return `Offer marked sent: ${name}.`
  if (state === 'not_needed') return `Marked not needed: ${name}.`
  return `Back in the queue: ${name}.`
}

/** The line under the queue's title: what it holds and why. */
export const QUEUE_NOTE =
  'Every client who left a contact and a trip, oldest first, until someone sends the written offer. Only a consultant quotes a fare: the assistant never does.'
