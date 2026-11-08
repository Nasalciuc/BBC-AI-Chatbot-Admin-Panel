// What Team says — pure, pinned by tests/team-view.test.ts. The numbers come
// from GET /api/admin/team/now and /admin/team/people/{id}/today
// (app/services/team_now.py): the server's own Ready windows, the shared
// queue's rule, takes and first replies as on the Dashboard.
import type { FloorAttention, FloorPerson, FloorPresence, PersonDay, TeamNow } from '../../lib/types.ts'

/** Who opens Team: the owner, admins, devs, supervisors, project managers. */
export const TEAM_ROLES = ['owner', 'admin', 'dev', 'supervisor', 'project_manager']

export function canSeeTeam(role: string | undefined): boolean {
  return !!role && TEAM_ROLES.includes(role)
}

export const PRESENCE: Record<FloorPresence, { label: string; dot: string; text: string }> = {
  ready: { label: 'Ready', dot: 'bg-k-00a645', text: 'text-k-0a6b35' },
  no_signal: { label: 'Ready, no signal', dot: 'bg-k-d97a00', text: 'text-k-8a4b00' },
  online: { label: 'Online, not Ready', dot: 'bg-k-9c9c9c', text: 'text-k-555555' },
  offline: { label: 'Offline', dot: 'bg-k-d9d9d9', text: 'text-k-6b6b6b' },
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`
}

function hhmm(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

/** A live timer: "0:51", "3:11", then "34 min" past ten minutes. */
export function timer(since: string | null | undefined, nowMs: number): string {
  if (!since) return ''
  const s = Math.max(0, Math.floor((nowMs - new Date(since).getTime()) / 1000))
  if (s >= 600) return `${Math.floor(s / 60)} min`
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

/** The timer's colour: red past 5 min, amber past 2, green under. */
export function timerTone(since: string | null | undefined, nowMs: number): 'bad' | 'warn' | 'ok' {
  const s = since ? (nowMs - new Date(since).getTime()) / 1000 : 0
  return s >= 300 ? 'bad' : s >= 120 ? 'warn' : 'ok'
}

export function secondsText(s: number | null | undefined): string {
  if (s === null || s === undefined) return '—'
  return s < 120 ? `${s} s` : `${Math.round(s / 60)} min`
}

export function minutesText(m: number): string {
  if (m < 60) return `${m} min`
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} m`
}

function firstOf(name: string | null | undefined): string {
  return (name ?? '').trim().split(/\s+/)[0] || 'Someone'
}

/** The four cards at the top of Now. */
export function statCards(d: TeamNow): { label: string; value: string; unit: string; sub: string; tone: 'ink' | 'bad' | 'warn' }[] {
  const s = d.stats!
  return [
    {
      label: 'Ready now',
      value: String(s.ready),
      unit: `of ${s.online} online`,
      sub: s.no_signal ? `${plural(s.no_signal, 'more is', 'more are')} Ready but ${s.no_signal === 1 ? 'sends' : 'send'} no signal` : 'Everyone Ready is in the queue',
      tone: 'ink',
    },
    {
      label: 'Waiting for a person',
      value: String(s.waiting),
      unit: s.waiting === 1 ? 'chat' : 'chats',
      sub: s.oldest_waiting ? `Oldest: ${s.oldest_waiting.client || 'a client'}, ${timer(s.oldest_waiting.since, new Date(d.now).getTime())}` : 'Nobody in the queue',
      tone: s.waiting ? 'bad' : 'ink',
    },
    {
      label: 'First reply today',
      value: s.first_reply_today_s === null ? '—' : secondsText(s.first_reply_today_s),
      unit: s.first_reply_today_s === null ? '' : 'median',
      sub: s.replies_today ? `From Take to the first message, ${plural(s.replies_today, 'chat')}` : 'No first reply yet today',
      tone: 'ink',
    },
    {
      label: 'Back to the assistant today',
      value: String(s.back_today),
      unit: s.back_today === 1 ? 'chat' : 'chats',
      sub: 'No first reply within 90 s',
      tone: s.back_today ? 'warn' : 'ink',
    },
  ]
}

export interface AttentionLine {
  key: string
  tone: 'bad' | 'warn' | 'muted'
  who: string
  text: string
  action: { label: string; kind: 'chat' | 'person' | 'queue' | 'coverage'; id: string }
}

/** One line of Needs attention, in words. */
export function attentionLine(a: FloorAttention): AttentionLine {
  switch (a.kind) {
    case 'client_waiting':
      return {
        key: `cw-${a.conversation_id}`, tone: 'bad', who: a.name ?? 'Someone',
        text: `No message in ${a.client || 'the client'}’s chat for ${a.minutes} min.`,
        action: { label: 'Open chat', kind: 'chat', id: a.conversation_id },
      }
    case 'no_signal':
      return {
        key: `ns-${a.user_id}`, tone: 'warn', who: a.name ?? 'Someone',
        text: `Ready, but the panel has sent no signal for ${a.minutes ?? '?'} min, so they’re out of the queue. Ready switches off at ${a.off_after_minutes} min.`,
        action: { label: 'See the day', kind: 'person', id: a.user_id },
      }
    case 'queue_wait': {
      const n = a.ready_chats
      const same = n.length > 0 && n.every((x) => x === n[0])
      const list = n.length > 1 ? `${n.slice(0, -1).join(', ')} and ${n[n.length - 1]}` : String(n[0] ?? 0)
      const loads = !n.length
        ? ' Nobody is Ready.'
        : n.length === 1
          ? ` The one person Ready has ${plural(n[0], 'chat')}.`
          : same
            ? ` The ${n.length} people Ready have ${plural(n[0], 'chat')} each.`
            : ` The ${n.length} people Ready have ${list} chats.`
      return {
        key: `qw-${a.tunnel}`, tone: 'warn', who: `${a.tunnel === 'support' ? 'Support' : 'Sales'} queue`,
        text: `${a.client || 'A client'} has waited ${a.minutes} min.${loads}`,
        action: { label: 'Open the queue', kind: 'queue', id: a.conversation_id },
      }
    }
    case 'coverage':
      return {
        key: `cv-${a.tunnel}`, tone: 'warn', who: a.tunnel === 'support' ? 'Support' : 'Sales',
        text: `Only 1 person Ready${a.since ? ` since ${hhmm(a.since)}` : ''} (${a.name ?? 'someone'}).`,
        action: { label: 'See who’s on', kind: 'coverage', id: a.user_id },
      }
    case 'went_back': {
      const off = a.presence === 'offline' && a.last_seen_at ? ` Offline since ${hhmm(a.last_seen_at)}.` : ''
      return {
        key: `wb-${a.user_id}`, tone: 'muted', who: a.name ?? 'Someone',
        text: `A chat went back to the assistant at ${hhmm(a.at)}: no first reply within 90 s.${off}`,
        action: { label: 'See the day', kind: 'person', id: a.user_id },
      }
    }
  }
}

/** The status line on a card "With a person". */
export function withPersonLine(
  c: { client: string; client_waiting: boolean; since: string | null; agent_name: string | null },
  nowMs: number,
): { text: string; tone: 'bad' | 'warn' | 'muted' } {
  const agent = firstOf(c.agent_name)
  const client = !c.client || c.client.startsWith('•') ? 'The client' : firstOf(c.client)
  const t = timer(c.since, nowMs)
  if (c.client_waiting) {
    const tone = timerTone(c.since, nowMs)
    return { text: `${client} wrote ${t} ago · no reply from ${agent} yet`, tone: tone === 'ok' ? 'muted' : tone }
  }
  return { text: `${agent} replied ${t} ago · waiting for ${client === 'The client' ? 'the client' : client}`, tone: 'muted' }
}

/** "Only problems": a client waiting past 2 min, or a queue chat past 2 min. */
export function isProblem(since: string | null | undefined, nowMs: number, waiting: boolean): boolean {
  return waiting && timerTone(since, nowMs) !== 'ok'
}

/** Coverage cells: how many were Ready in that hour. */
export function coverageCell(n: number | null): string {
  if (n === null) return 'bg-k-f7f7f7'
  if (n === 0) return 'bg-k-ececec'
  if (n === 1) return 'bg-k-e3f6ea'
  if (n === 2) return 'bg-k-5fd394'
  return 'bg-k-00a645'
}

export function coverageNote(cov: (number | null)[]): string {
  const known = cov.filter((x): x is number => x !== null)
  if (!known.length) return 'The day hasn’t started'
  if (known.every((x) => x === 0)) return 'Nobody Ready yet today'
  const ones = known.filter((x) => x === 1).length
  return ones >= 3 ? `Only 1 person Ready for ${ones} h` : 'Covered so far'
}

export function shiftText(start: string | null | undefined, end: string | null | undefined): string {
  const s = start?.slice(0, 5)
  const e = end?.slice(0, 5)
  return s && e ? `shift ${s}–${e}` : 'no shift set'
}

/** The roster's status column. */
export function rosterStatus(
  u: { is_active: boolean; role: string; last_seen_at: string | null },
  p: FloorPerson | undefined,
): { label: string; dot: string } {
  if (!u.is_active) return { label: 'Deactivated', dot: 'bg-k-d93025' }
  if (p) return { label: PRESENCE[p.presence].label, dot: PRESENCE[p.presence].dot }
  return { label: 'Active', dot: 'bg-k-9c9c9c' }
}

/** The person panel's eight tiles. */
export function dayTiles(d: PersonDay): { value: string; label: string; tone: 'ink' | 'bad' | 'warn' }[] {
  const t = d.today
  return [
    { value: String(t.chats_now), label: 'chats now', tone: 'ink' },
    { value: String(t.took), label: 'taken today', tone: 'ink' },
    { value: secondsText(t.first_reply_median_s), label: 'first reply', tone: t.first_reply_median_s !== null && t.first_reply_median_s > 60 ? 'warn' : 'ink' },
    { value: t.minutes_online ? minutesText(t.minutes_online) : '—', label: 'online', tone: 'ink' },
    { value: t.first_seen_at ? hhmm(t.first_seen_at) : '—', label: 'first seen', tone: 'ink' },
    { value: String(t.replied_to), label: 'replied to', tone: 'ink' },
    { value: String(t.went_back), label: 'back to the assistant', tone: t.went_back ? 'bad' : 'ink' },
    { value: String(t.takes_lost), label: 'Takes lost', tone: 'ink' },
  ]
}

/** Ready today as bars on a 24 h line: left and width in percent. */
export function readyBars(d: Pick<PersonDay, 'ready_today' | 'day_start'>, nowMs: number): { left: number; width: number }[] {
  const start = new Date(d.day_start).getTime()
  const day = 86_400_000
  return d.ready_today.map(([a, b]) => {
    const x = Math.max(0, new Date(a).getTime() - start)
    const y = Math.min(day, (b ? new Date(b).getTime() : nowMs) - start)
    return { left: (x / day) * 100, width: Math.max(0.4, ((y - x) / day) * 100) }
  })
}

/** One line of "Activity today". */
export function activityText(e: PersonDay['activity'][number], first: string): { text: string; tone: 'ok' | 'bad' | 'muted' } {
  const c = e.client || 'a client'
  switch (e.kind) {
    case 'ready':
      return { text: 'Ready', tone: 'ok' }
    case 'not_ready':
      return { text: 'Not ready', tone: 'muted' }
    case 'took':
      return { text: `Took ${c}’s chat`, tone: 'ok' }
    case 'replied':
      return { text: `Replied to ${c} in ${secondsText(e.seconds ?? 0)}`, tone: 'ok' }
    case 'went_back':
      return { text: `${c}’s chat went back to the assistant: no first reply within 90 s`, tone: 'bad' }
    case 'closed':
      return { text: `Closed ${c}’s chat`, tone: 'muted' }
    case 'take_lost':
      return { text: `Pressed Take on ${c}’s chat; someone else got it first`, tone: 'muted' }
    case 'client_waiting':
      return { text: `${c} wrote; no message from ${first} since`, tone: 'bad' }
  }
}

/** "Ready since 13:08" / "Ready, but no signal" / "Offline · last seen 11:20". */
export function presenceLine(u: PersonDay['user']): { text: string; tone: string } {
  switch (u.presence) {
    case 'ready':
      return { text: u.ready_since ? `Ready since ${hhmm(u.ready_since)}` : 'Ready', tone: 'text-k-0a6b35' }
    case 'no_signal':
      return { text: 'Ready, but the panel sends no signal: out of the queue', tone: 'text-k-8a4b00' }
    case 'online':
      return { text: 'Online, not Ready', tone: 'text-k-555555' }
    case 'offline':
      return { text: u.last_seen_at ? `Offline · last seen ${hhmm(u.last_seen_at)}` : 'Offline', tone: 'text-k-6b6b6b' }
  }
}

/** The page's first line: whose team, and the time. */
export function scopeLine(role: string, teamNames: string[], nowMs: number): string {
  const d = new Date(nowMs)
  const when = `${d.toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'short' })}, ${hhmm(d.toISOString())}`
  if (role === 'supervisor') return `Your team: ${teamNames[0] ?? '—'} · ${hhmm(d.toISOString())}`
  if (role === 'project_manager') return `Your teams: ${teamNames.join(', ') || '—'} · ${hhmm(d.toISOString())}`
  return `All teams · ${when}`
}

/** The text of "How chats reach people", from the server's own windows. */
export function howChatsReachPeople(w: { queue_s: number; ready_off_s: number }): string[] {
  return [
    'New chats go to a shared queue. Everyone Ready in that tunnel sees them; the first to press Take gets the chat.',
    'A client who chatted with us in the last 90 days goes straight back to the same person, if that person is at their desk.',
    'After Take, the first reply has 90 seconds (a little longer while the chat is open on screen). Otherwise the assistant answers again.',
    `Close the panel or leave the CRM, and you drop out of the queue within ${w.queue_s} seconds. Ready switches off after ${Math.round(w.ready_off_s / 60)} minutes without a signal.`,
    'If a client asks for a person and nobody is Ready, the managers get an e-mail.',
  ]
}
