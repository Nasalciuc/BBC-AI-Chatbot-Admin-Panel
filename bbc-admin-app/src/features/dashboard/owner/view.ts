// What the Dashboard says — pure, so every sentence is pinned by
// tests/owner-dashboard-view.test.ts. Numbers come from GET /api/dashboard/owner
// (owner, admin, dev) or /api/dashboard/team (supervisor, PM, QA).
import type { AttentionItem, LeaveRow, OwnerDashboard, SourceRow } from '../../../lib/types.ts'

export type Range = 1 | 7 | 30
export type Tone = 'good' | 'bad' | 'same'

/** Whose Dashboard: the owner's (owner, admin, dev), a team's (supervisor and
 *  project manager: their tunnel) or QA's (all chats, quality first). */
export type DashVariant = 'owner' | 'team' | 'qa'
export type DashTab = 'overview' | 'conversion' | 'agents' | 'quality'

export const TAB_LABEL: Record<DashTab, string> = {
  overview: 'Overview',
  conversion: 'Conversion',
  agents: 'Agents',
  quality: 'Quality',
}

export const TABS_FOR: Record<DashVariant, DashTab[]> = {
  owner: ['overview', 'conversion', 'agents', 'quality'],
  team: ['overview', 'agents'],
  qa: ['quality', 'conversion', 'agents'],
}

/** The first line under the title: whose chats these are. */
export function scopeText(d: Pick<OwnerDashboard, 'sites' | 'scope'>, variant: DashVariant): string {
  if (variant === 'owner') return `${sitesText(d.sites)} · all chats`
  const tunnel = d.scope?.tunnel ?? 'all'
  return tunnel === 'all' ? 'All chats' : `All ${tunnel} chats`
}

/** On a phone only the first tab is shown; this names the rest. */
export function phoneNote(variant: DashVariant): string {
  const rest = TABS_FOR[variant].slice(1).map((t) => TAB_LABEL[t])
  if (!rest.length) return ''
  const names = rest.length === 1 ? rest[0] : `${rest.slice(0, -1).join(', ')} and ${rest[rest.length - 1]}`
  return `${names} open${rest.length === 1 ? 's' : ''} on a computer.`
}

export function pct(n: number, d: number): number | null {
  return d > 0 ? Math.round((n / d) * 100) : null
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`
}

/** "the 7 days before" / "yesterday at this time". */
export function beforeWords(days: Range): string {
  return days === 1 ? 'yesterday at this time' : `the ${days} days before`
}

/**
 * The change against the previous period. `upIsGood` decides the colour:
 * more ready leads is good, more chats back to the assistant is not.
 */
export function delta(
  now: number,
  before: number,
  days: Range,
  { upIsGood = true, unit = '' }: { upIsGood?: boolean; unit?: string } = {}
): { text: string; tone: Tone } | null {
  const d = now - before
  if (d === 0) return { text: `Same as ${beforeWords(days)}`, tone: 'same' }
  const up = d > 0
  return {
    text: `${up ? '▲' : '▼'} ${Math.abs(d)}${unit} vs ${beforeWords(days)}`,
    tone: up === upIsGood ? 'good' : 'bad',
  }
}

export function money(x: number | null | undefined): string {
  if (x === null || x === undefined) return '—'
  return `$${x < 10 ? x.toFixed(2) : Math.round(x).toLocaleString('en-US')}`
}

export function secondsText(s: number | null | undefined): string {
  if (s === null || s === undefined) return '—'
  if (s < 90) return `${Math.round(s)} s`
  return `${(s / 60).toFixed(1)} min`
}

/** 8 → "8a", 12 → "12p", 14 → "2p" (chart ticks); long form "2 pm". */
export function hourTick(h: number): string {
  const n = h % 12 === 0 ? 12 : h % 12
  return `${n}${h < 12 ? 'a' : 'p'}`
}

export function hourLong(h: number): string {
  const n = h % 12 === 0 ? 12 : h % 12
  return h === 0 ? 'midnight' : h === 12 ? 'noon' : `${n} ${h < 12 ? 'am' : 'pm'}`
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso.length <= 10 ? `${iso}T12:00:00` : iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}

export function dayTick(date: string, withDay: boolean): string {
  const d = new Date(`${date}T12:00:00`)
  const wd = d.toLocaleDateString('en-GB', { weekday: 'short' })
  return withDay ? `${wd} ${d.getDate()}` : wd
}

/** "Buy Business Class and Business Class Tickets". */
export function sitesText(sites: string[]): string {
  if (!sites.length) return 'All sites'
  if (sites.length === 1) return sites[0]
  return `${sites.slice(0, -1).join(', ')} and ${sites[sites.length - 1]}`
}

/** "last 7 days, 31 Oct – 6 Nov" / "today, Fri 6 Nov". */
export function rangeText(d: Pick<OwnerDashboard, 'days' | 'range_start' | 'now' | 'tz_offset_minutes'>): string {
  const local = (iso: string) => new Date(new Date(iso).getTime() + d.tz_offset_minutes * 60_000)
  const fmt = (x: Date, wd = false) =>
    `${wd ? `${x.toLocaleDateString('en-GB', { weekday: 'short', timeZone: 'UTC' })} ` : ''}${x.getUTCDate()} ${x.toLocaleDateString('en-GB', { month: 'short', timeZone: 'UTC' })}`
  const a = local(d.range_start)
  const b = local(d.now)
  return d.days === 1 ? `today, ${fmt(b, true)}` : `last ${d.days} days, ${fmt(a)} – ${fmt(b)}`
}

/** The city of the browser's time zone: "New York time". */
export function zoneText(tz?: string): string {
  const zone = tz ?? Intl.DateTimeFormat().resolvedOptions().timeZone ?? ''
  const city = zone.split('/').pop()?.replace(/_/g, ' ')
  return city ? `${city} time` : 'Local time'
}

// ── Needs attention ───────────────────────────────────────────────────

export interface AttentionView {
  key: string
  level: 'urgent' | 'check'
  tag: string
  title: string
  sub: string
  action: { label: string; to: 'agents' | 'hour' | 'review' | 'quality' | 'agent-table' }
}

export function attentionView(item: AttentionItem, qaAccounts: number): AttentionView {
  switch (item.kind) {
    case 'agent':
      return {
        key: `agent-${item.user_id}`,
        level: 'urgent',
        tag: 'Urgent',
        title: `${item.went_back} of ${item.name}’s ${item.chats} chats went back to the assistant.`,
        sub: `First reply: ${secondsText(item.first_reply_s)}. Ready ${item.ready_hours} h.`,
        action: { label: 'See Agents', to: 'agents' },
      }
    case 'hour':
      return {
        key: `hour-${item.hour}`,
        level: 'check',
        tag: 'Check',
        title: `At ${hourLong(item.hour)}, ${item.went_back} of ${item.given} chats went back to the assistant.`,
        sub: `${plural(item.ready_people, 'person', 'people')} ${item.ready_people === 1 ? 'was' : 'were'} Ready at that hour; the most chats back to the assistant of any hour.`,
        action: { label: 'See by hour', to: 'hour' },
      }
    case 'review':
      return {
        key: 'review',
        level: 'check',
        tag: 'Check',
        title: `${plural(item.waiting, 'ready lead')} ${item.waiting === 1 ? 'is' : 'are'} waiting for review.`,
        sub: `${item.last_review_at ? `Nobody has reviewed one since ${shortDate(item.last_review_at)}.` : 'No ready lead has been reviewed yet.'} ${plural(qaAccounts, 'QA account')}. Reviews are saved on the lead.`,
        action: { label: 'Start reviewing', to: 'review' },
      }
    case 'ai_latency':
      return {
        key: 'ai',
        level: 'check',
        tag: 'Check',
        title: `The AI’s typical answer takes ${(item.latency_ms / 1000).toFixed(1)} s.`,
        sub: `The line is ${item.line_ms / 1000} s. Clients wait for every answer.`,
        action: { label: 'See Quality', to: 'quality' },
      }
  }
}

export const ATTENTION_LINES =
  'Shown only past a line: a first reply over 60 s with a third of an agent’s chats back to the assistant (3 chats or more), an hour with 3 or more chats back, a ready lead waiting more than 2 days for review, an AI answer over 5 s.'

// ── Headlines ─────────────────────────────────────────────────────────

function worstHour(d: OwnerDashboard): number | null {
  const rows = d.agents.by_hour.filter((h) => h.went_back > 0)
  if (!rows.length) return null
  return rows.reduce((a, b) => (b.went_back > a.went_back ? b : a)).hour
}

export function overviewHeadline(d: OwnerDashboard): string {
  const now = d.overview.now.ready
  const before = d.overview.before.ready
  const days = d.days
  let first: string
  if (!now && !before) first = 'No ready leads in this period.'
  else if (!before) first = `${plural(now, 'ready lead')}, none ${beforeWords(days).replace('the ', 'in the ')}.`
  else {
    const change = Math.round(((now - before) / before) * 100)
    first =
      change === 0
        ? `Ready leads are the same as ${beforeWords(days)}.`
        : `Ready leads are ${change > 0 ? 'up' : 'down'} ${Math.abs(change)}% on ${beforeWords(days)}.`
  }
  const top = d.attention[0]
  if (!top) return `${first} Nothing crossed a line.`
  if (top.kind === 'agent' || top.kind === 'hour') {
    const back = d.agents.now.went_back
    const hour = worstHour(d)
    return `${first} The weak spot is the agents: ${plural(back, 'chat')} went back to the assistant${hour !== null ? `, most at ${hourLong(hour)}` : ''}.`
  }
  if (top.kind === 'review') return `${first} ${plural(top.waiting, 'ready lead')} ${top.waiting === 1 ? 'is' : 'are'} waiting for review.`
  return `${first} The AI answers slower than the line.`
}

export function agentsHeadline(d: OwnerDashboard): string {
  const back = d.agents.now.went_back
  if (!d.agents.now.given) return 'No chat was given to an agent in this period.'
  if (!back) return `${plural(d.agents.now.given, 'chat')} given to agents, none went back to the assistant.`
  const urgent = d.attention.find((a) => a.kind === 'agent')
  if (!urgent || urgent.kind !== 'agent') return `${plural(back, 'chat')} went back to the assistant. Nobody is past the line.`
  const mostReady = d.agents.by_agent.reduce((a, b) => (b.ready_hours > a.ready_hours ? b : a), d.agents.by_agent[0])
  const rest = back - urgent.went_back
  const others = new Set(d.agents.by_agent.filter((a) => a.user_id !== urgent.user_id && a.went_back > 0).map((a) => a.user_id)).size
  const ready = mostReady?.user_id === urgent.user_id ? ` Ready the most hours (${urgent.ready_hours} h), but` : ''
  const tail = rest > 0 ? ` The other ${rest} ${rest === 1 ? 'is' : 'are'} spread across ${plural(others, 'person', 'people')}.` : ''
  return `${urgent.went_back} of the ${back} chats that went back to the assistant were ${urgent.name}’s.${ready}${ready ? ' a' : ' A'} first reply of ${secondsText(urgent.first_reply_s)}.${tail}`
}

export function qualityHeadline(d: OwnerDashboard): string {
  const q = d.quality
  if (!q.ready) return 'No ready leads in this period, so nothing to review.'
  if (!q.to_review) return `All ${q.ready} ready leads in this period are reviewed.`
  const since = q.last_review_at ? `Nobody has reviewed a ready lead since ${shortDate(q.last_review_at)}.` : 'No ready lead has been reviewed yet.'
  return `${since} ${q.to_review} ${q.to_review === 1 ? 'is' : 'are'} waiting in this period. Start with the oldest.`
}

// ── Conversion ────────────────────────────────────────────────────────

export interface Change {
  key: string
  title: string
  text: string
  chats: LeaveRow['conversations']
  count: number
}

const QUESTION: Partial<Record<LeaveRow['key'], string>> = {
  from: '“Where are you flying from?”',
  to: 'the question about where they fly to',
  route: '“Where are you flying from and to?”',
  dates: 'the question about dates',
  flexible: 'the question about flexible dates',
  travelers: 'the question about how many travelers',
  occasion: 'the question about the occasion',
  contact: 'the question about a phone or an e-mail',
  summary: 'the summary',
}

/** At most two, from fixed rules: one question that loses a fifth of the
 *  people who leave, and a promise of a person people leave after. */
export function changesThisWeek(leave: OwnerDashboard['leave'], days: Range): Change[] {
  const out: Change[] = []
  const total = leave.total
  const q = leave.rows.find((r) => r.key in QUESTION && r.chats >= 3 && total > 0 && r.chats / total >= 0.2)
  if (q) {
    const d = q.chats - q.before
    const vs = d > 0 ? `, ${d} more than ${beforeWords(days)}` : d < 0 ? `, ${-d} fewer than ${beforeWords(days)}` : ''
    out.push({
      key: q.key,
      title:
        q.key === 'from'
          ? 'Ask where they fly to first, then where from.'
          : q.key === 'summary'
            ? 'Look at the summary people don’t confirm.'
            : `Look at ${QUESTION[q.key]}.`,
      text: `${q.chats} of the ${total} people who left did it right after ${QUESTION[q.key]}${vs}.${q.key === 'from' ? ' The destination is the question people came with.' : ''}`,
      chats: q.conversations,
      count: q.chats,
    })
  }
  const promise = leave.rows.find((r) => r.key === 'specialist' && r.chats >= 3)
  if (promise) {
    out.push({
      key: 'specialist',
      title: 'Don’t promise a person when nobody is Ready.',
      text: `${plural(promise.chats, 'person', 'people')} left after “a specialist will join”. Let the AI take the request and say when someone will call.`,
      chats: promise.conversations,
      count: promise.chats,
    })
  }
  return out.slice(0, 2)
}

export const FUNNEL_STEPS = [
  'Gave the route',
  '+ a date in the future',
  '+ travelers and the return',
  'Confirmed the trip',
  'Ready to quote',
]

/** Best and worst source by ready-if-they-wrote, among sources with 5+ writers. */
export function sourceSentence(rows: SourceRow[], tab: string): string {
  const named = rows.filter((r) => r.name !== 'Other' && r.wrote >= 5)
  if (named.length < 2) return ''
  const rate = (r: SourceRow) => r.ready / r.wrote
  const best = named.reduce((a, b) => (rate(b) > rate(a) ? b : a))
  const worst = named.reduce((a, b) => (rate(b) < rate(a) ? b : a))
  if (best === worst) return ''
  const thing = tab === 'source' ? '' : ` (${tab})`
  return `${best.name}${thing} turns the most writers into ready leads (${pct(best.ready, best.wrote)}%). ${worst.name} the fewest (${pct(worst.ready, worst.wrote)}%).`
}

// ── Agents ────────────────────────────────────────────────────────────

/** "1 in 9 took longer than 90 s". */
export function overDeadlineText(over: number, measured: number): string {
  if (!measured) return 'No first reply measured yet.'
  if (!over) return `None took longer than 90 s.`
  const n = Math.max(1, Math.round(measured / over))
  return `1 in ${n} took longer than 90 s.`
}

export type AgentSortKey = 'ready_hours' | 'chats' | 'first_reply_median_s' | 'went_back' | 'after_yes' | 'never_wrote' | 'not_in_crm'

/** Click a column to sort, worst first. */
export function sortAgents<T extends Record<AgentSortKey, number | null> & { name: string }>(rows: T[], key: AgentSortKey): T[] {
  // For Ready hours and chats "worst" is the fewest; for everything else, the most.
  const fewestFirst = key === 'ready_hours'
  return [...rows].sort((a, b) => {
    const x = a[key] ?? -1
    const y = b[key] ?? -1
    if (x !== y) return fewestFirst ? x - y : y - x
    return a.name.localeCompare(b.name)
  })
}

// ── Quality ───────────────────────────────────────────────────────────

export const AI_REVIEW_TEXT: Record<'phone' | 'price' | 'repeat', { title: string; text: string }> = {
  phone: { title: 'Gave the phone number', text: 'The AI wrote “call +1 888…”.' },
  price: { title: 'Asked for a price', text: 'The client asked what it costs; the consultant prices it.' },
  repeat: { title: 'The AI repeated itself', text: 'The same message twice or more.' },
}
