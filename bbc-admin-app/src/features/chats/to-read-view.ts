// What To read says — pure, pinned by tests/to-read-view.test.ts.
// The rows and the rules come from GET /api/conversations/to-read
// (app/services/to_read.py): fixed rules, no AI. "Read" is kept in this
// browser only (nothing on the server records who read what).
import type { LostReason, Reading, ReadingEvent, ToReadList, ToReadRow } from '../../lib/types.ts'
import { initials } from './view.ts'

export const REASON: Record<LostReason, { label: string; tone: 'bad' | 'warn'; title: (name: string) => string; text: (name: string) => string }> = {
  back: {
    label: 'Went back to the assistant',
    tone: 'bad',
    title: () => 'Nobody answered in time, and the assistant took the chat back.',
    text: (name) => `${name} didn’t send a first message within 90 s.`,
  },
  anyone: {
    label: '“Anyone there?”',
    tone: 'bad',
    title: () => 'The client asked if anyone was there.',
    text: (name) => `${name} had the chat when the client asked.`,
  },
  never: {
    label: 'Never wrote',
    tone: 'bad',
    title: (name) => `Given to ${name}, but nobody wrote.`,
    text: (name) => `${name} had the chat and sent nothing, and it didn’t go back to the assistant.`,
  },
  yes: {
    label: 'After a YES',
    tone: 'warn',
    title: () => 'The client had confirmed the trip to the assistant before the take.',
    text: (name) => `Read whether ${name} asked for the trip again.`,
  },
}

/** The rule in a "because …" sentence. */
const WHY: Record<LostReason, string> = {
  back: 'the chat went back to the assistant',
  anyone: 'the client asked if anyone was there',
  never: 'nobody wrote',
  yes: 'the person came in after the client’s YES',
}

export const SAMPLE_LABEL: Record<'sample' | 'help', string> = {
  sample: 'Picked at random',
  help: 'One more: needs help',
}

function localDay(iso: string): string {
  const d = new Date(iso)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** The list's three groups, for one person or everyone. */
export function groupsOf(list: ToReadList, who: string, nowMs: number): { today: ToReadRow[]; earlier: ToReadRow[]; sample: ToReadRow[] } {
  const mine = (r: ToReadRow) => who === 'all' || r.agent_id === who
  const today = localDay(new Date(nowMs).toISOString())
  const lost = list.lost.filter(mine)
  return {
    today: lost.filter((r) => localDay(r.at) === today),
    earlier: lost.filter((r) => localDay(r.at) !== today),
    sample: list.sample.filter(mine),
  }
}

/** "Mark read" is per week: a chat read last week can come back this week. */
export function readKey(list: Pick<ToReadList, 'week_start'>, row: Pick<ToReadRow, 'id' | 'kind'>): string {
  return `${list.week_start.slice(0, 10)}:${row.kind === 'lost' ? 'lost' : 'sample'}:${row.id}`
}

export function unreadCount(list: ToReadList | undefined, read: Set<string>): number {
  if (!list) return 0
  return [...list.lost, ...list.sample].filter((r) => !read.has(readKey(list, r))).length
}

/** The next row to read after `id`, in the list's order, unread first. */
export function nextUnread(rows: ToReadRow[], list: ToReadList, read: Set<string>, id: string): ToReadRow | null {
  const i = rows.findIndex((r) => r.id === id)
  const after = [...rows.slice(i + 1), ...rows.slice(0, Math.max(0, i))]
  return after.find((r) => !read.has(readKey(list, r))) ?? null
}

/** "2 not read · today" / "all read". */
export function groupNote(rows: ToReadRow[], list: ToReadList, read: Set<string>, tail?: string): string {
  const n = rows.filter((r) => !read.has(readKey(list, r))).length
  const head = n ? `${n} not read` : 'all read'
  return tail ? `${head} · ${tail}` : head
}

/** The row's second line: whose chat, where to. */
export function rowLine(r: Pick<ToReadRow, 'agent_name' | 'route'>): string {
  return [r.agent_name ?? 'Nobody', r.route].filter(Boolean).join(' · ')
}

/** The row's label: the first rule, "+ the next one". */
export function rowLabel(r: ToReadRow): { text: string; more: string | null; tone: 'bad' | 'warn' | 'muted' | 'ai' } {
  if (r.kind !== 'lost') return { text: SAMPLE_LABEL[r.kind], more: null, tone: r.kind === 'help' ? 'ai' : 'muted' }
  const [first, ...rest] = r.reasons
  if (!first) return { text: 'Lost', more: null, tone: 'bad' }
  return {
    text: REASON[first.key].label,
    more: rest.length ? `+ ${rest.map((x) => REASON[x.key].label).join(', ')}` : null,
    tone: REASON[first.key].tone,
  }
}

/** A supervisor gets a masked name ("•••cu"): the avatar shows "#". */
export function avatarText(client: string): string {
  return !client || client.startsWith('•') ? '#' : initials(client)
}

export function clientName(client: string, chatNumber: number | null): string {
  if (client && !client.startsWith('•')) return client
  return [chatNumber ? `#${chatNumber}` : null, client || null].filter(Boolean).join(' · ') || 'A client'
}

function first(name: string | null | undefined): string {
  return (name ?? '').trim().split(/\s+/)[0] || 'The agent'
}

/** The panel's title and the line under the rules. */
export function sideTitle(r: Pick<ToReadRow, 'kind' | 'agent_name' | 'reasons'>): { kicker: string; title: string; why: string } {
  const who = r.agent_name ?? 'Nobody'
  if (r.kind === 'sample') {
    return { kicker: `From your team · ${who}`, title: `A normal chat of ${first(r.agent_name)}’s, picked at random.`, why: 'Samples show everyday work, not only what went wrong.' }
  }
  if (r.kind === 'help') {
    return {
      kicker: `From your team · ${who}`,
      title: `One more chat of ${first(r.agent_name)}’s: last week was past the help line.`,
      why: 'The line: 3 or more chats, a first reply over 60 s and a third of them back to the assistant.',
    }
  }
  const main = r.reasons[0]
  const named = r.reasons.map((x) => WHY[x.key])
  return {
    kicker: `Lost · ${who}`,
    title: main ? REASON[main.key].title(first(main.name ?? r.agent_name)) : 'Lost.',
    why: `In “Lost” because ${named.length > 1 ? `${named.slice(0, -1).join(', ')} and ${named[named.length - 1]}` : named[0] ?? 'of a rule'}. These come from fixed rules, no AI.`,
  }
}

/** "14:28:07" — the side timeline is to the second. */
export function clockSeconds(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return [d.getHours(), d.getMinutes(), d.getSeconds()].map((x) => String(x).padStart(2, '0')).join(':')
}

/** One line of "What happened", and whether it is a rule broken. */
export function eventText(e: ReadingEvent, client: string): { text: string; bad: boolean } {
  const name = e.name ?? 'Someone'
  switch (e.kind) {
    case 'started':
      return { text: `${client || 'The client'} started the chat`, bad: false }
    case 'taken':
      return { text: `${name} had the chat`, bad: false }
    case 'first_reply':
      return { text: `First reply · ${e.seconds ?? 0} s`, bad: (e.seconds ?? 0) > 60 }
    case 'went_back':
      return { text: 'No reply for 90 s → back to the assistant', bad: true }
    case 'closed':
      return { text: `${name} closed the chat`, bad: false }
    case 'not_ready':
      return { text: `${name} went Not ready`, bad: false }
    case 'ended':
      return { text: 'The chat ended', bad: false }
  }
}

/** The marker in the conversation where a rule was broken. */
export function markerOf(key: LostReason, name: string | null): { title: string; text: string; tone: 'bad' | 'warn' } {
  const who = first(name)
  switch (key) {
    case 'back':
      return { title: 'Here the chat went back to the assistant.', text: `${who} didn’t reply for 90 s.`, tone: 'bad' }
    case 'anyone':
      return { title: 'The client asked if anyone was there.', text: `${who} had the chat.`, tone: 'bad' }
    case 'never':
      return { title: 'Never wrote.', text: `${who} had the chat and sent nothing.`, tone: 'bad' }
    case 'yes':
      return { title: 'Came in after a YES.', text: `The client had confirmed the trip to the assistant before ${who} took it.`, tone: 'warn' }
  }
}

/** The lead in one sentence, as the side panel says it. */
export function leadSentence(lead: NonNullable<Reading['lead']>): string {
  const dates = [lead.departure_date, lead.return_date].filter(Boolean).map((d) => {
    const x = new Date(`${d}T12:00:00`)
    return `${x.getDate()} ${x.toLocaleDateString('en-GB', { month: 'short' })}`
  })
  const parts = [
    lead.route ?? 'No route yet',
    dates.length ? dates.join(' – ') : null,
    lead.passengers ? `${lead.passengers} ${lead.passengers === 1 ? 'traveler' : 'travelers'}` : null,
  ].filter(Boolean)
  const level = { ready: 'Ready to quote', almost: 'Almost ready', early: 'Early interest', contact: 'Contact only' }[lead.level]
  const crm =
    lead.crm_state === 'in_crm'
      ? `In the CRM${lead.crm_lead_id ? ` · lead ${lead.crm_lead_id}` : ''}`
      : lead.crm_state === 'no_phone'
        ? 'No phone, so not in the CRM'
        : 'Not in the CRM'
  return `${parts.join(', ')}. ${level}. ${crm}.`
}

/** Where the reasons are in the conversation: the message (or the time) to jump to. */
export function anchorFor(key: LostReason): string {
  return `mark-${key}`
}

export function weekLine(w: NonNullable<Reading['week']>): string {
  return `${w.chats} ${w.chats === 1 ? 'chat' : 'chats'} this week, ${w.went_back} went back to the assistant.`
}
