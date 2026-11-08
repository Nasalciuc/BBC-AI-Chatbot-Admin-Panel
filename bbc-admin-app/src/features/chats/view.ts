/**
 * What the Conversations screens SAY — one place, pure, node-testable.
 *
 * Every label comes from data the server keeps today (the design canvas's
 * "what we can build now" pages). Nothing here invents a field: when the
 * server does not know something, the text says so instead of guessing.
 *
 * Relative imports only (the zero-dependency node test runner resolves no
 * path aliases). Class names are written out whole so Tailwind sees them.
 */

import type { Conversation, Lead, Message } from '../../lib/types.ts'

// ── names and time ─────────────────────────────────────────────────────

export function initials(name: string | null | undefined): string {
  const parts = (name ?? '').trim().split(/\s+/).filter(Boolean)
  if (!parts.length) return '?'
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
}

/** "Michael Turner" → "Michael"; no name → "The client". */
export function firstName(name: string | null | undefined): string {
  const first = (name ?? '').trim().split(/\s+/)[0]
  return first || 'The client'
}

export function clock(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

/** Today → "14:12"; another day → "14 Oct". */
export function rowTime(iso: string | null | undefined, now: Date): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  if (d.toDateString() === now.toDateString()) return clock(iso)
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}

export function minutesSince(iso: string | null | undefined, nowMs: number): number | null {
  if (!iso) return null
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return null
  return Math.max(0, Math.floor((nowMs - t) / 60_000))
}

/** 0 → "just now", 4 → "4 min", 65 → "1 h 5 min", 2880 → "2 days". */
export function minutesLabel(min: number): string {
  if (min < 1) return 'just now'
  if (min < 60) return `${min} min`
  if (min < 24 * 60) {
    const h = Math.floor(min / 60)
    const m = min % 60
    return m ? `${h} h ${m} min` : `${h} h`
  }
  const days = Math.floor(min / (24 * 60))
  return days === 1 ? '1 day' : `${days} days`
}

export function countdown(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

export function seconds(s: number | null | undefined): string {
  if (s === null || s === undefined) return '—'
  if (s < 90) return `${s} s`
  return minutesLabel(Math.round(s / 60))
}

// ── presence ───────────────────────────────────────────────────────────

export interface PresenceView {
  state: 'here' | 'away' | 'left' | 'quiet' | 'unknown'
  label: string
  dot: string
  text: string
}

const FRESH_LEFT_S = 120

/**
 * The client's presence as the server derives it (online / minimized / stale /
 * left + seconds since the last widget signal). A fresh "left" is usually a
 * page change, so it does not read as gone for the first two minutes.
 */
export function presenceView(
  effective: string | null | undefined,
  ageSeconds: number | null | undefined
): PresenceView {
  const age = typeof ageSeconds === 'number' ? Math.floor(ageSeconds / 60) : null
  switch (effective) {
    case 'online':
      return { state: 'here', label: 'On the site now', dot: 'bg-k-00a645', text: 'text-k-0a6b35' }
    case 'minimized':
      return { state: 'away', label: 'On the site · chat window closed', dot: 'bg-k-9c9c9c', text: 'text-k-555555' }
    case 'stale':
      return {
        state: 'quiet',
        label: age !== null ? `No signal for ${minutesLabel(age)}` : 'No signal from the site',
        dot: 'bg-k-c9c9c9',
        text: 'text-k-6b6b6b',
      }
    case 'left':
      if (typeof ageSeconds === 'number' && ageSeconds < FRESH_LEFT_S) {
        return { state: 'away', label: 'Left just now', dot: 'bg-k-9c9c9c', text: 'text-k-555555' }
      }
      return {
        state: 'left',
        label: age !== null ? `Left ${minutesLabel(age)} ago` : 'Left the site',
        dot: 'bg-k-c9c9c9',
        text: 'text-k-6b6b6b',
      }
    default:
      // No signal at all: say nothing rather than claim the client is gone.
      return { state: 'unknown', label: '', dot: 'bg-k-c9c9c9', text: 'text-k-6b6b6b' }
  }
}

/** presence from a detail payload's metadata (the list carries it on the row). */
export function presenceFromMeta(meta: Record<string, unknown> | undefined): {
  effective: string | null
  ageSeconds: number | null
} {
  const m = meta ?? {}
  const eff =
    (typeof m.widget_presence_effective === 'string' && m.widget_presence_effective) ||
    (typeof m.widget_presence === 'string' && m.widget_presence) ||
    null
  let age: number | null = null
  if (typeof m.widget_presence_age_seconds === 'number') age = m.widget_presence_age_seconds
  else if (typeof m.widget_last_event_at === 'string') {
    const t = new Date(m.widget_last_event_at).getTime()
    if (!Number.isNaN(t)) age = Math.max(0, Math.floor((Date.now() - t) / 1000))
  }
  return { effective: eff, ageSeconds: age }
}

/** After the client leaves, the stale_presence job closes the chat 5 min later. */
export const STALE_CLOSE_MIN = 5

// ── whose chat, and what needs doing ───────────────────────────────────

export type Owner = 'mine' | 'ai' | 'agent' | 'waiting' | 'closed'

export function ownerOf(c: Conversation, meId: string | undefined): Owner {
  if (c.status === 'closed') return 'closed'
  // Assigned is assigned, whatever the mode: a silent reservation
  // (status needs_agent, assigned to me) is mine to answer before it is human.
  if (c.assigned_agent_id) return c.assigned_agent_id === meId ? 'mine' : 'agent'
  if (c.status === 'needs_agent' || c.mode === 'waiting_for_agent' || c.mode === 'human') return 'waiting'
  return 'ai'
}

/** Avatar tint by whose chat it is (the canvas's colors). */
export function avatarClasses(owner: Owner): string {
  switch (owner) {
    case 'waiting':
      return 'bg-k-fff3e0 text-k-8a4b00'
    case 'closed':
      return 'bg-k-ececec text-k-6b6b6b'
    case 'mine':
      return 'bg-k-00c450/15 text-k-0a6b35'
    case 'ai':
      return 'bg-k-efeaff text-k-4a36c4'
    default:
      return 'bg-k-e9e9e9 text-k-444444'
  }
}

/** A client waiting this long for a person's reply turns the row red. */
export const NO_REPLY_RED_MIN = 5

export interface Signal {
  text: string
  /** 0 red (no reply), 1 amber (your turn / just taken), 3 neutral */
  urgency: 0 | 1 | 3
  clock: boolean
}

/** Seconds left before an unanswered take goes back to the assistant, or null. */
export function deadlineLeft(
  c: Pick<Conversation, 'mode' | 'status' | 'assigned_agent_id' | 'last_agent_message_at' | 'metadata'>,
  nowMs: number,
  deadlineSeconds: number
): number | null {
  if (c.mode !== 'human' || c.status === 'closed' || !c.assigned_agent_id) return null
  const at = c.metadata?.agent_assigned_at
  if (typeof at !== 'string') return null
  const t0 = new Date(at).getTime()
  if (Number.isNaN(t0)) return null
  const lastAgent = c.last_agent_message_at ? new Date(c.last_agent_message_at).getTime() : NaN
  if (!Number.isNaN(lastAgent) && lastAgent >= t0) return null // answered since the take
  const left = deadlineSeconds - Math.floor((nowMs - t0) / 1000)
  // Past zero the sweep (every 15 s) has not run yet: "0:00" until it does.
  return Math.max(0, left)
}

/** Minutes the client has been waiting for a person's reply, or null. */
export function noReplyMinutes(
  c: Pick<Conversation, 'mode' | 'assigned_agent_id' | 'last_user_message_at' | 'last_agent_message_at'>,
  nowMs: number
): number | null {
  if (c.mode !== 'human' || !c.assigned_agent_id) return null
  const u = c.last_user_message_at ? new Date(c.last_user_message_at).getTime() : NaN
  if (Number.isNaN(u)) return null
  const a = c.last_agent_message_at ? new Date(c.last_agent_message_at).getTime() : NaN
  if (!Number.isNaN(a) && a >= u) return null
  return Math.max(0, Math.floor((nowMs - u) / 60_000))
}

export function signalOf(
  c: Conversation,
  owner: Owner,
  nowMs: number,
  deadlineSeconds: number
): Signal | null {
  if (owner === 'closed') {
    const at = c.closed_at ?? c.updated_at
    return { text: at ? `Closed ${rowTime(at, new Date(nowMs))}` : 'Closed', urgency: 3, clock: false }
  }
  if (owner === 'mine') {
    const left = deadlineLeft(c, nowMs, deadlineSeconds)
    if (left !== null) return { text: `Just taken · reply within ${countdown(left)}`, urgency: 1, clock: true }
  }
  const waiting = noReplyMinutes(c, nowMs)
  if (waiting !== null && waiting >= NO_REPLY_RED_MIN) {
    return { text: `No reply ${minutesLabel(waiting)}`, urgency: 0, clock: true }
  }
  if (owner === 'mine' && waiting !== null) {
    return { text: `Your turn · ${minutesLabel(waiting)}`, urgency: 1, clock: true }
  }
  return null
}

export function signalClasses(s: Signal): string {
  return s.urgency === 0 ? 'text-k-b3261e' : s.urgency === 1 ? 'text-k-8a4b00' : 'text-k-6b6b6b'
}

/** "You", "AI is answering", "Rohan Mehta" — the owner line in All active. */
export function ownerLine(c: Conversation, owner: Owner): { text: string; cls: string } {
  if (owner === 'mine') return { text: 'You', cls: 'text-k-0a6b35' }
  if (owner === 'ai') {
    if (c.agent_state === 'fallback' && c.engaged_agent_name)
      return { text: `${c.engaged_agent_name} → assistant`, cls: 'text-k-4a36c4' }
    return { text: 'AI is answering', cls: 'text-k-4a36c4' }
  }
  if (owner === 'waiting') return { text: 'Waiting for a person', cls: 'text-k-8a4b00' }
  if (owner === 'closed') return { text: 'Closed', cls: 'text-k-6b6b6b' }
  return { text: c.assigned_agent_name ?? 'An agent', cls: 'text-k-444444' }
}

/** "You: …", "AI: …", "Rohan: …" or the client's own words. */
export function previewText(c: Conversation): string {
  const m = c.last_message
  if (!m?.content) return ''
  if (m.role === 'ai') return `AI: ${m.content}`
  if (m.role === 'agent') return `${c.assigned_agent_name ? firstName(c.assigned_agent_name) : 'Agent'}: ${m.content}`
  return m.content
}

export function routeText(c: Pick<Conversation, 'route'>): string {
  return c.route ? `${c.route.from} → ${c.route.to}` : ''
}

/** A waiting row (taken blind): whether they asked for a person and how
 *  much they wrote. Never where they fly, never their words. */
export function queueLine(c: Pick<Conversation, 'status' | 'message_count'>): string {
  if (c.status === 'needs_agent') return 'Asked for a person'
  const n = c.message_count ?? 0
  return n === 1 ? '1 message' : `${n} messages`
}

// ── the thread ─────────────────────────────────────────────────────────

export interface ThreadLine {
  id: string
  kind: 'system' | 'message'
  who: Message['role']
  text: string
  at: string
  showLabel: boolean
  showDay: boolean
  day: string
}

/** Messages → lines: same-speaker turns grouped, a day line when the date changes. */
export function threadLines(messages: Message[]): ThreadLine[] {
  let prevWho: string | null = null
  let prevDay: string | null = null
  return messages.map((m) => {
    const d = new Date(m.created_at)
    const day = Number.isNaN(d.getTime()) ? '' : d.toDateString()
    const showDay = !!day && day !== prevDay
    prevDay = day || prevDay
    if (m.role === 'system') {
      prevWho = null
      return { id: m.id, kind: 'system', who: m.role, text: m.content, at: m.created_at, showLabel: false, showDay, day }
    }
    const showLabel = showDay || prevWho !== m.role
    prevWho = m.role
    return { id: m.id, kind: 'message', who: m.role, text: m.content, at: m.created_at, showLabel, showDay, day }
  })
}

// ── what the assistant collected ───────────────────────────────────────

const CABIN: Record<string, string> = { business: 'Business', first: 'First', economy: 'Economy', mixed: 'Mixed' }
const TRIP: Record<string, string> = {
  one_way: 'One way',
  round_trip: 'Round trip',
  multi_city: 'Multi-city',
  open_jaw: 'Open jaw',
  tour: 'Tour',
}

export function dayMonth(date: string | null | undefined): string {
  if (!date) return ''
  const d = new Date(`${date.slice(0, 10)}T12:00:00`)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}

/** "25 Nov 2026": with the year, as the old panel showed it and a quote
 *  needs it (a December chat about January). One line on the Trip card. */
export function fullDate(date: string | null | undefined): string {
  if (!date) return ''
  const d = new Date(`${date.slice(0, 10)}T12:00:00`)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
}

export interface Row {
  label: string
  value: string
  strong?: boolean
  warn?: boolean
  /** A copy button: what goes on the clipboard, and what to call it. */
  copy?: { text: string; what: string }
}

/** Who flies. `passengers` is everyone; the CRM takes adults as the rest
 *  (crm.py), so the split shown is the one the CRM gets. */
export function travelersText(lead: Pick<Lead, 'passengers' | 'children_count' | 'infant_count'>): string {
  const total = lead.passengers ?? 0
  if (total <= 0) return ''
  const head = total === 1 ? '1 traveler' : `${total} travelers`
  const kids = Math.max(0, lead.children_count ?? 0)
  const babies = Math.max(0, lead.infant_count ?? 0)
  if (!kids && !babies) return head
  const adults = Math.max(1, total - kids - babies)
  const parts = [`${adults} ${adults === 1 ? 'adult' : 'adults'}`]
  if (kids) parts.push(`${kids} ${kids === 1 ? 'child' : 'children'}`)
  if (babies) parts.push(`${babies} ${babies === 1 ? 'infant' : 'infants'}`)
  return `${head} · ${parts.join(', ')}`
}

export interface LeadNotes {
  /** Multi-city: every leg, the real itinerary (lead_service). */
  itinerary: string
  /** null: no call asked. '': asked, no number caught. Else the number. */
  callback: string | null
  /** Lines a person wrote — never rewritten by the pipeline. */
  other: string[]
}

/** lead.notes as the pipeline writes it: "Itinerary: …", the "Chat: …" line
 *  (the signals, shown as rows instead), "CLIENT ASKED TO BE CALLED — …",
 *  and anything a person added. The old panel showed the notes raw. */
export function notesOf(lead: Pick<Lead, 'notes'> | null | undefined): LeadNotes {
  const out: LeadNotes = { itinerary: '', callback: null, other: [] }
  for (const raw of (lead?.notes ?? '').split('\n')) {
    const line = raw.trim()
    if (!line || line.startsWith('Chat: ')) continue
    if (line.startsWith('Itinerary: ')) {
      out.itinerary = line.slice('Itinerary: '.length).trim()
    } else if (line.startsWith('CLIENT ASKED TO BE CALLED')) {
      const n = line.split('—').slice(1).join('—').trim()
      out.callback = n && n !== 'number not captured' ? n : ''
    } else {
      out.other.push(line)
    }
  }
  return out
}

const said = (v: unknown): string => (typeof v === 'string' ? v.trim() : '')

/** Everything the assistant collected about the trip — what the old panel
 *  showed (route, dates with the year, travelers, class, notes) plus the
 *  answers it keeps only in intent_signals. Dates and the itinerary copy. */
export function tripRows(lead: Lead | null | undefined, signals: Record<string, unknown>): Row[] {
  if (!lead) return []
  const oneWay = lead.trip_type === 'one_way'
  const notes = notesOf(lead)
  const rows: Row[] = []
  if (notes.itinerary) rows.push({ label: 'Itinerary', value: notes.itinerary, copy: { text: notes.itinerary, what: 'Itinerary' } })
  const out = fullDate(lead.departure_date)
  if (out) rows.push({ label: oneWay ? 'When' : 'Out', value: out, copy: { text: out, what: 'Departure date' } })
  const back = fullDate(lead.return_date)
  if (back) rows.push({ label: 'Back', value: back, copy: { text: back, what: 'Return date' } })
  // flexible_dates is true on every lead (column default); only the
  // client's own answer says flexible or fixed.
  const dates = signals.date_flexible === true ? 'Flexible' : signals.date_flexible === false || lead.flexible_dates === false ? 'Fixed' : ''
  if (dates) rows.push({ label: 'Dates', value: dates })
  const who = travelersText(lead)
  if (who) rows.push({ label: 'Travelers', value: who })
  if (lead.cabin_class) rows.push({ label: 'Cabin', value: CABIN[lead.cabin_class] ?? lead.cabin_class })
  if (lead.trip_type) {
    rows.push({
      label: 'Trip',
      value: (TRIP[lead.trip_type] ?? lead.trip_type) + (signals.nonstop_only === true ? ' · direct only' : ''),
    })
  }
  if (said(signals.airline_preference)) rows.push({ label: 'Airlines', value: said(signals.airline_preference) })
  if (said(signals.airline_avoid)) rows.push({ label: 'Not with', value: said(signals.airline_avoid) })
  if (said(signals.budget_hint)) rows.push({ label: 'Budget', value: said(signals.budget_hint) })
  if (said(signals.price_seen)) rows.push({ label: 'Price seen', value: said(signals.price_seen) })
  if (said(signals.must_haves)) rows.push({ label: 'Must have', value: said(signals.must_haves) })
  for (const n of notes.other) rows.push({ label: 'Note', value: n, copy: { text: n, what: 'Note' } })
  return rows
}

/** The route as the Trip card's big line; '' while it isn't known. */
export function routeLine(lead: Pick<Lead, 'origin_code' | 'destination_code'> | null | undefined): string {
  if (lead?.origin_code && lead?.destination_code) return `${lead.origin_code} → ${lead.destination_code}`
  return lead?.origin_code || lead?.destination_code || ''
}

/** The whole trip as plain lines: for the CRM's notes, an e-mail, a colleague. */
export function tripText(route: string, rows: Row[]): string {
  return [route, ...rows.map((r) => `${r.label}: ${r.value}`)].filter(Boolean).join('\n')
}

/** What a lead still lacks before the assistant can call it Ready. */
export function missingForReady(lead: Lead | null | undefined): string[] {
  if (!lead) return []
  const out: string[] = []
  if (!lead.departure_date) out.push('a departure date')
  if (lead.trip_type !== 'one_way' && !lead.return_date) out.push('the return date')
  if (!lead.passengers) out.push('travelers')
  if (lead.tier !== 'gold') out.push('the client’s yes')
  return out
}

export function joinAnd(items: string[]): string {
  if (items.length <= 1) return items[0] ?? ''
  return `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`
}

export interface Level {
  name: string
  why: string
  steps: number
  ready: boolean
  chip: string
}

/** The lead's tier in words. gold = the client confirmed to the assistant. */
export function levelOf(lead: Lead | null | undefined): Level {
  if (!lead || (!lead.origin_code && !lead.destination_code)) {
    return { name: 'Contact only', why: 'the form, no trip yet', steps: 1, ready: false, chip: 'Contact' }
  }
  if (lead.tier === 'gold') {
    return { name: 'Ready to quote', why: 'the client confirmed the trip to the assistant', steps: 4, ready: true, chip: 'Ready' }
  }
  if (lead.tier === 'silver' || lead.departure_date) {
    return { name: 'Almost ready', why: 'from the assistant: route and dates, no yes yet', steps: 3, ready: false, chip: 'Almost' }
  }
  return { name: 'Early interest', why: 'from the assistant: route only', steps: 2, ready: false, chip: 'Early' }
}

const PERSONA: Record<string, [string, string]> = {
  time_is_money: ['Values time', 'Wants to arrive rested and on schedule.'],
  experience_seeker: ['Wants an experience', 'The trip itself matters, not only the price.'],
  needs_based: ['Needs comfort', 'Comfort and care come first.'],
  value_driven: ['Looks for value', 'Wants a good deal for the money.'],
}
const OCCASION: Record<string, string> = {
  business: 'Work trip',
  celebration: 'Celebration',
  family: 'Family visit',
  medical: 'Medical trip',
  honeymoon: 'Honeymoon',
  vacation: 'Holiday',
}

/** The person, not the trip (airlines moved to the Trip card). A call back
 *  carries the number the client gave for it, which may not be the form's. */
export function aboutOf(
  signals: Record<string, unknown>,
  lead?: Pick<Lead, 'notes'> | null
): {
  persona: { label: string; why: string } | null
  rows: Row[]
} {
  const p = typeof signals.persona === 'string' ? PERSONA[signals.persona] : undefined
  const rows: Row[] = []
  if (typeof signals.occasion === 'string') {
    rows.push({ label: 'Occasion', value: OCCASION[signals.occasion] ?? signals.occasion })
  }
  if (said(signals.booking_for)) rows.push({ label: 'Booking for', value: said(signals.booking_for) })
  const callback = notesOf(lead).callback
  if (callback) rows.push({ label: 'Asks', value: `A call back on ${callback}`, copy: { text: callback, what: 'Number' } })
  else if (signals.callback_requested === true || callback === '') rows.push({ label: 'Asks', value: 'A call back' })
  if (said(signals.best_call_time)) rows.push({ label: 'Best time', value: said(signals.best_call_time) })
  return { persona: p ? { label: p[0], why: p[1] } : null, rows }
}

export function signalsOf(lead: Lead | null | undefined): Record<string, unknown> {
  const s = lead?.intent_signals
  return s && typeof s === 'object' && !Array.isArray(s) ? s : {}
}

// ── the summary, honestly ──────────────────────────────────────────────

/** The assistant writes the summary ONCE, at the client's 4th message, and
 *  never rewrites it (orchestrator: history is capped at 10 messages). */
export function noSummaryText(
  c: Conversation,
  owner: Owner,
  clientMessages: number
): string {
  const first = firstName(c.visitor_name)
  const rule = 'The assistant writes it once, at the client’s 4th message'
  if (owner === 'closed' && clientMessages < 4) return `No summary. The chat ended before ${first}’s 4th message.`
  if (owner === 'waiting' || owner === 'ai') {
    return clientMessages < 4
      ? `No summary yet. ${rule}. ${first} has sent ${clientMessages}.`
      : 'No summary for this chat.'
  }
  return `No summary. ${rule}, and ${first} came to a person before that.${c.tunnel === 'support' ? '' : ' The trip below is what it collected.'}`
}

export function summaryStamp(c: Conversation): string {
  return `Written once by the assistant, at ${firstName(c.visitor_name)}’s 4th message. It isn’t updated after that.`
}

export function tripNote(c: Conversation, owner: Owner): string {
  if (c.tunnel === 'support') return ''
  const first = firstName(c.visitor_name)
  if (owner === 'waiting') return `From the assistant. Ask ${first} for what’s missing once you take the chat.`
  if (owner === 'ai') return 'The assistant is still filling this in.'
  if (owner === 'closed') return `From the assistant. What ${first} said to a person in the chat isn’t added here.`
  const whose = owner === 'mine' ? 'yours' : c.assigned_agent_name ? `${firstName(c.assigned_agent_name)}’s` : 'an agent’s'
  return `From the assistant, before the chat was ${whose}. What ${first} ${owner === 'mine' ? 'tells you' : 'says'} in the chat isn’t added here.`
}

// ── the CRM ────────────────────────────────────────────────────────────

export type CrmState = 'in' | 'blocked' | 'missing' | 'ready' | 'none'

export interface CrmView {
  state: CrmState
  title: string
  text: string
  dot: string
  fg: string
}

/** Plain words for the quality gate's refusal codes (crm_push_gate_reason). */
const GATE: Record<string, string> = {
  email_invalid: 'the e-mail doesn’t look valid',
  test_email: 'it looks like a test e-mail',
  email_typo_domain: 'the e-mail domain looks misspelled',
  same_route: 'the departure and the destination are the same',
  low_score: 'the lead is too thin to send',
  past_date: 'the travel date is in the past',
  phone_format: 'the phone number doesn’t look valid',
}

export function gateReasonText(code: string | null | undefined): string {
  if (!code) return ''
  return GATE[code] ?? code.replace(/_/g, ' ')
}

export function crmView(
  c: Conversation,
  lead: Lead | null | undefined,
  opts: { canAct: boolean; ownerName: string | null }
): CrmView {
  const first = firstName(c.visitor_name)
  if (!lead) {
    return { state: 'none', title: 'No lead yet', text: 'The assistant creates one once the client leaves a contact.', dot: 'bg-k-9c9c9c', fg: 'text-k-555555' }
  }
  if (lead.created_in_crm) {
    return {
      state: 'in',
      title: `In the CRM${lead.crm_lead_id ? ` · lead ${lead.crm_lead_id}` : ''}`,
      text: '',
      dot: 'bg-k-00a645',
      fg: 'text-k-0a6b35',
    }
  }
  if (c.tag === 'abandoned' || c.tag === 'no_engagement') {
    return {
      state: 'blocked',
      title: 'Not sent to the CRM',
      text:
        c.tag === 'no_engagement'
          ? `${first} left a contact but never wrote a message.`
          : `${first} went quiet before the chat was finished.`,
      dot: 'bg-k-9c9c9c',
      fg: 'text-k-555555',
    }
  }
  const phone = (lead.visitor_phone ?? c.visitor_phone ?? '').trim()
  const email = (lead.visitor_email ?? c.visitor_email ?? '').trim()
  const miss = [!phone && 'a phone number', !email && 'an e-mail'].filter(Boolean) as string[]
  if (miss.length) {
    return {
      state: 'missing',
      title: 'Not in the CRM yet',
      text: `The CRM needs a phone and an e-mail. Still missing: ${joinAnd(miss)}.${opts.canAct && c.status !== 'closed' ? ` Ask ${first} in the chat.` : ''}`,
      dot: 'bg-k-d97a00',
      fg: 'text-k-8a4b00',
    }
  }
  const gate = gateReasonText(lead.crm_push_gate_reason)
  const base = c.mode === 'ai' && c.status !== 'closed'
    ? `The assistant sends it when ${first} confirms the trip, or after the chat goes quiet if the lead qualifies.`
    : opts.canAct
      ? c.status === 'closed'
        ? `The chat closed without sending it. Send it now, or add ${first} in the CRM yourself and save the lead number here.`
        : `Closing the chat doesn’t send it. Send it now, or add ${first} in the CRM yourself and save the lead number here.`
      : `Closing the chat doesn’t send it. ${opts.ownerName ? `${firstName(opts.ownerName)} can send it from the chat.` : 'Whoever takes the chat can send it.'}`
  return {
    state: 'ready',
    title: 'Not in the CRM yet',
    text: gate ? `${base} Last try was refused: ${gate}.` : base,
    dot: 'bg-kb-202020',
    fg: 'text-k-202020',
  }
}

/** The close dialog's sentence, by CRM state. */
export function closeSentence(c: Conversation, crm: CrmView, lead: Lead | null | undefined): string {
  const first = firstName(c.visitor_name)
  if (crm.state === 'in') return `It moves to Closed. ${first} is in the CRM.`
  if (crm.state === 'ready') return `${first} isn’t in the CRM, and closing the chat doesn’t send it.`
  if (crm.state === 'missing') {
    const phone = (lead?.visitor_phone ?? c.visitor_phone ?? '').trim()
    const email = (lead?.visitor_email ?? c.visitor_email ?? '').trim()
    const what = !phone && !email ? 'phone or e-mail' : !phone ? 'phone' : 'e-mail'
    return `${first} isn’t in the CRM: there’s no ${what} yet. Closing doesn’t send anything.`
  }
  return 'It moves to Closed.'
}

// ── where they came from ───────────────────────────────────────────────

export function deviceOf(ua: unknown): string {
  if (typeof ua !== 'string' || !ua) return ''
  if (/iPad|Tablet/i.test(ua)) return 'Tablet'
  if (/iPhone|Android|Mobile/i.test(ua)) return 'Phone'
  return 'Computer'
}

export function whereRows(meta: Record<string, unknown> | undefined, phoneCountry?: string | null): Row[] {
  const m = (meta ?? {}) as Record<string, unknown>
  const s = (k: string) => (typeof m[k] === 'string' ? (m[k] as string).trim() : '')
  const rows: Row[] = []
  const site = s('site')
  if (site) rows.push({ label: 'Site', value: site === 'bbc' ? 'buybusinessclass.com' : site === 'bct' ? 'businessclass-tickets.com' : site })
  const ads = s('gclid') ? 'Google Ads' : s('msclkid') ? 'Microsoft Ads' : s('fbclid') ? 'Facebook ad' : ''
  const src = [s('utm_source'), s('utm_medium')].filter(Boolean).join(' · ')
  if (ads || src) rows.push({ label: 'Source', value: [ads, src].filter(Boolean).join(' · ') })
  if (s('utm_campaign')) rows.push({ label: 'Campaign', value: s('utm_campaign') })
  if (s('utm_term')) rows.push({ label: 'Searched', value: `“${s('utm_term')}”` })
  const page = s('page_url').replace(/^https?:\/\/[^/]+/, '')
  if (s('page_url')) rows.push({ label: 'Page', value: page || '/' })
  if (s('referrer')) {
    let host = s('referrer')
    try {
      host = new URL(host).hostname
    } catch {
      /* not a URL: show what was stored */
    }
    rows.push({ label: 'From', value: host })
  }
  const device = deviceOf(m.user_agent)
  if (device) rows.push({ label: 'Device', value: device })
  if (phoneCountry) rows.push({ label: 'Phone from', value: phoneCountry })
  return rows
}
