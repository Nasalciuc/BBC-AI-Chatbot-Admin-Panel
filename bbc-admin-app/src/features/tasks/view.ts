// Tasks — every sentence and time the page shows, pure (tests/tasks-view.test.ts).
// Times are written in the business zone ("New York time"); the client's
// time is a guess from the trip's departure city, so it names the city.

import type { TaskAlertRow, TaskAlerts, TaskKind, TaskRow, TaskState, TasksView } from '@/lib/types'

export const KIND_LABEL: Record<TaskKind, string> = { call: 'Call', email: 'E-mail', todo: 'To-do' }
export const KIND_FILTERS: { key: TaskKind | null; label: string }[] = [
  { key: null, label: 'All' },
  { key: 'call', label: 'Calls' },
  { key: 'email', label: 'E-mails' },
  { key: 'todo', label: 'To-dos' },
]

/** The zone's short city name, for "New York time". */
/**
 * The zone times are SHOWN in: the viewer's own clock, like the chat's
 * timestamps and the rest of the panel. The business zone (task rules) only
 * reads promises and decides what "today" is.
 */
export function localZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}

/** Same pause as the API's "wrote again": a quick "thanks" after the promise isn't coming back. */
export const CAME_BACK_AFTER_MS = 10 * 60_000

/** The client's first message after a pause following the promise: where "came back to the chat" goes. */
export function cameBackMessage(messages: { id: string; role: string; created_at: string }[], madeMs: number | null): string | null {
  if (madeMs === null) return null
  return messages.find((m) => m.role === 'user' && Date.parse(m.created_at) > madeMs + CAME_BACK_AFTER_MS)?.id ?? null
}

export function zoneCity(tz: string): string {
  const city = tz.split('/').pop() || tz
  return city.replace(/_/g, ' ')
}

// ── Zone arithmetic without a library ───────────────────────────────────────

type Wall = { y: number; mo: number; d: number; h: number; mi: number; wd: number }

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

/** The wall clock in `tz` at an instant. */
export function wallClock(ms: number, tz: string): Wall {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: tz, year: 'numeric', month: 'numeric', day: 'numeric', hour: 'numeric', minute: 'numeric', weekday: 'short', hourCycle: 'h23',
  }).formatToParts(new Date(ms))
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? ''
  return { y: +get('year'), mo: +get('month'), d: +get('day'), h: +get('hour') % 24, mi: +get('minute'), wd: WEEKDAYS.indexOf(get('weekday')) }
}

/** The instant a wall clock time in `tz` happens (DST-safe for the hours we use). */
export function zonedInstant(y: number, mo: number, d: number, h: number, mi: number, tz: string): number {
  let guess = Date.UTC(y, mo - 1, d, h, mi)
  for (let i = 0; i < 2; i++) {
    const w = wallClock(guess, tz)
    const drift = Date.UTC(w.y, w.mo - 1, w.d, w.h, w.mi) - Date.UTC(y, mo - 1, d, h, mi)
    guess -= drift
  }
  return guess
}

const pad = (n: number) => String(n).padStart(2, '0')
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export function clockIn(ms: number, tz: string): string {
  const w = wallClock(ms, tz)
  return `${pad(w.h)}:${pad(w.mi)}`
}

export function dayIn(ms: number, tz: string): string {
  const w = wallClock(ms, tz)
  return `${WEEKDAYS[w.wd]} ${w.d} ${MONTHS[w.mo - 1]}`
}

function sameDay(a: number, b: number, tz: string): boolean {
  const x = wallClock(a, tz)
  const y = wallClock(b, tz)
  return x.y === y.y && x.mo === y.mo && x.d === y.d
}

/** "13 min", "2 h 40 min", "3 h", "2 d". */
export function span(ms: number): string {
  const min = Math.max(1, Math.round(Math.abs(ms) / 60_000))
  if (min < 60) return `${min} min`
  const h = Math.floor(min / 60)
  if (h < 48) return min % 60 ? `${h} h ${min % 60} min` : `${h} h`
  return `${Math.round(h / 24)} d`
}

// ── Due ─────────────────────────────────────────────────────────────────────

/**
 * A task made with a day and no time is due by 23:00 in its maker's clock. It
 * reads as just a day in that clock; in another zone the deadline is shown as
 * the time it really is there (Tue 23:00 New York is Wed 06:00 in Chisinau).
 */
function dayOnly(r: Pick<TaskRow, 'due_kind'>, at: number, tz: string): boolean {
  return r.due_kind === 'day' && clockIn(at, tz) === '23:00'
}

/** The big due text: "15:07" today, "Mon 9 Nov, 10:00" another day, "Tue 10 Nov" with no time. */
export function dueMain(r: Pick<TaskRow, 'due_at' | 'due_kind'>, nowMs: number, tz: string): string {
  if (!r.due_at) return 'No time set'
  const at = Date.parse(r.due_at)
  if (dayOnly(r, at, tz)) return dayIn(at, tz)
  return sameDay(at, nowMs, tz) ? clockIn(at, tz) : `${dayIn(at, tz)}, ${clockIn(at, tz)}`
}

/** The client's time, named by the city it's guessed from: "13:40 in Chicago", "Atlanta time too". */
export function clientTime(r: Pick<TaskRow, 'due_at' | 'client_city' | 'client_zone'>, tz: string): string | null {
  if (!r.due_at || !r.client_city || !r.client_zone) return null
  const at = Date.parse(r.due_at)
  const theirs = wallClock(at, r.client_zone)
  const ours = wallClock(at, tz)
  if (theirs.h === ours.h && theirs.mi === ours.mi && theirs.d === ours.d) return `${r.client_city} time too`
  const day = theirs.d !== ours.d ? `${WEEKDAYS[theirs.wd]} ` : ''
  return `${day}${pad(theirs.h)}:${pad(theirs.mi)} in ${r.client_city}`
}

/** The small line under the due time: how late or how long, why that time, and the client's time. */
export function dueSub(
  r: Pick<TaskRow, 'due_at' | 'due_kind' | 'state' | 'client_city' | 'client_zone'>,
  nowMs: number,
  tz: string,
): string {
  if (!r.due_at) return ''
  const at = Date.parse(r.due_at)
  const bits: string[] = []
  if (r.state === 'late') bits.push(`${span(nowMs - at)} late`)
  else if (r.state === 'today') bits.push(`in ${span(at - nowMs)}`)
  if (r.due_kind === 'default') bits.push('no time said')
  else if (r.due_kind === 'end_of_day') bits.push('“today” = end of the working day')
  else if (dayOnly(r, at, tz)) bits.push('no time set')
  // A day was never a time on the client's clock. "Today" ends on the business
  // clock: name the client's only when it reads differently from yours.
  const theirs = r.due_kind === 'day' ? null : clientTime(r, tz)
  if (theirs && !(r.due_kind === 'end_of_day' && theirs.endsWith(' time too'))) bits.push(theirs)
  return bits.join(' · ')
}

// ── Lines ───────────────────────────────────────────────────────────────────

export function travelers(r: Pick<TaskRow, 'passengers' | 'children_count' | 'infant_count'>): string {
  const n = (r.passengers ?? 0) + (r.children_count ?? 0) + (r.infant_count ?? 0)
  return n > 0 ? `${n} traveler${n === 1 ? '' : 's'}` : ''
}

function shortDate(iso: string | null): string {
  if (!iso) return ''
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number)
  return y && m && d ? `${d} ${MONTHS[m - 1]}` : ''
}

/** "Michael R. · New York → London · 16 Nov · 2 travelers". */
export function clientLine(r: Pick<TaskRow, 'client' | 'route' | 'departure_date' | 'passengers' | 'children_count' | 'infant_count'>): string {
  return [r.client, r.route, shortDate(r.departure_date), travelers(r)].filter(Boolean).join(' · ')
}

/** Why nobody has it. */
export function unownedNote(r: Pick<TaskRow, 'unowned_reason' | 'unowned_from_name' | 'unowned_at'>, tz: string): string | null {
  if (r.unowned_reason === 'left_shift') {
    const who = r.unowned_from_name || 'Someone'
    return r.unowned_at ? `${who} went Not ready at ${clockIn(Date.parse(r.unowned_at), tz)}` : `${who} went Not ready`
  }
  if (r.unowned_reason === 'ai_only') return 'Asked in a chat with the AI'
  return null
}

export function wroteAgain(r: Pick<TaskRow, 'client' | 'client_wrote_at'>, tz: string): string | null {
  if (!r.client_wrote_at) return null
  const who = (r.client || 'The client').split(' ')[0]
  return `${who} wrote again ${clockIn(Date.parse(r.client_wrote_at), tz)}`
}

/** "2 late · 2 today · 2 later · 86% on time this week (12 of 14)". */
/**
 * A task's state in the viewer's clock. The API's today/later is the business
 * day; the page shows times in the viewer's zone, so "Today" must be their day
 * too, or a task due at 06:00 tomorrow would sit under Today. Late is late anywhere.
 */
export function shownState(r: Pick<TaskRow, 'due_at' | 'state'>, nowMs: number, tz: string): TaskState {
  if (r.state === 'done') return 'done'
  if (!r.due_at) return 'later'
  const at = Date.parse(r.due_at)
  if (at < nowMs) return 'late'
  return sameDay(at, nowMs, tz) ? 'today' : 'later'
}

/** The page's counts from the rows as shown (see shownState). */
export function countStates(rows: Pick<TaskRow, 'state' | 'assignee_id'>[]): TasksView['counts'] {
  const c = { late: 0, today: 0, later: 0, unowned: 0 }
  for (const r of rows) {
    if (r.state === 'late' || r.state === 'today' || r.state === 'later') c[r.state] += 1
    if (!r.assignee_id) c.unowned += 1
  }
  return c
}

export function summaryLine(v: Pick<TasksView, 'counts' | 'on_time'>): string {
  const parts = [`${v.counts.late} late`, `${v.counts.today} today`, `${v.counts.later} later`]
  if (v.on_time.due > 0) {
    parts.push(`${Math.round((v.on_time.on_time / v.on_time.due) * 100)}% on time this week (${v.on_time.on_time} of ${v.on_time.due})`)
  }
  return parts.join(' · ')
}

export type TaskGroup = { key: 'unowned' | 'late' | 'today' | 'later'; title: string; rows: TaskRow[]; note?: string }

/** Unowned first (for the people who give tasks out), then Overdue, Today, Later. */
export function groupTasks(rows: TaskRow[], withUnowned: boolean): TaskGroup[] {
  const unowned = withUnowned ? rows.filter((r) => !r.assignee_id) : []
  const rest = withUnowned ? rows.filter((r) => r.assignee_id) : rows
  const groups: TaskGroup[] = [
    { key: 'unowned', title: 'Unowned', rows: unowned, note: 'Nobody has it: the chat was only with the AI, or the person left the shift.' },
    { key: 'late', title: 'Overdue', rows: rest.filter((r) => r.state === 'late') },
    { key: 'today', title: 'Today', rows: rest.filter((r) => r.state === 'today') },
    { key: 'later', title: 'Later', rows: rest.filter((r) => r.state === 'later') },
  ]
  return groups.filter((g) => g.rows.length > 0)
}

// ── New due times ───────────────────────────────────────────────────────────

export type DueChoice = { label: string; at: number }

/** "+1 hour", "Tomorrow 10:00", "Monday 10:00" (the next Monday after tomorrow). */
export function snoozeChoices(nowMs: number, tz: string, fromMs?: number): DueChoice[] {
  const base = Math.max(nowMs, fromMs ?? nowMs)
  const w = wallClock(nowMs, tz)
  const tomorrow = wallClock(zonedInstant(w.y, w.mo, w.d, 12, 0, tz) + 86_400_000, tz)
  const toMonday = ((8 - w.wd) % 7) || 7
  const monday = wallClock(zonedInstant(w.y, w.mo, w.d, 12, 0, tz) + toMonday * 86_400_000, tz)
  const out: DueChoice[] = [
    { label: '+1 hour', at: base + 3_600_000 },
    { label: 'Tomorrow 10:00', at: zonedInstant(tomorrow.y, tomorrow.mo, tomorrow.d, 10, 0, tz) },
  ]
  if (toMonday > 1) out.push({ label: 'Monday 10:00', at: zonedInstant(monday.y, monday.mo, monday.d, 10, 0, tz) })
  return out
}

/** New task: "In 2 hours", "Today 17:00" (while it's ahead), "Tomorrow 10:00". */
export function newTaskChoices(nowMs: number, tz: string): DueChoice[] {
  const w = wallClock(nowMs, tz)
  const out: DueChoice[] = [{ label: 'In 2 hours', at: nowMs + 2 * 3_600_000 }]
  const five = zonedInstant(w.y, w.mo, w.d, 17, 0, tz)
  if (five > nowMs + 30 * 60_000) out.push({ label: 'Today 17:00', at: five })
  out.push(snoozeChoices(nowMs, tz)[1])
  return out
}

/** A date and time picked in the business zone, as an ISO instant. */
export function pickedInstant(date: string, time: string, tz: string): string | null {
  const [y, mo, d] = date.split('-').map(Number)
  const [h, mi] = (time || '10:00').split(':').map(Number)
  if (!y || !mo || !d || Number.isNaN(h) || Number.isNaN(mi)) return null
  return new Date(zonedInstant(y, mo, d, h, mi, tz)).toISOString()
}

// ── The chat: "Follow-up caught" and the late banner ────────────────────────

/** Under the agent's message: "Follow-up caught: e-mail by 15:07" / "Follow-up late: e-mail was due 15:07". */
export function caughtLine(r: Pick<TaskRow, 'kind' | 'due_at' | 'due_kind' | 'state'>, nowMs: number, tz: string): { head: string; text: string; late: boolean; done: boolean } {
  const what = r.kind === 'todo' ? 'follow-up' : KIND_LABEL[r.kind].toLowerCase()
  const when = r.due_at ? dueMain(r, nowMs, tz) : 'no time'
  if (r.state === 'done') return { head: 'Follow-up done:', text: `${what}`, late: false, done: true }
  if (r.state === 'late') return { head: 'Follow-up late:', text: `${what} was due ${when}`, late: true, done: false }
  return { head: 'Follow-up caught:', text: `${what} by ${when}`, late: false, done: false }
}

/** The banner over a chat with a late promise. */
export function lateBanner(r: Pick<TaskRow, 'due_at' | 'client' | 'client_wrote_at'>, nowMs: number, tz: string): { head: string; late: string; text: string } {
  const at = r.due_at ? Date.parse(r.due_at) : nowMs
  const who = (r.client || 'The client').split(' ')[0]
  const wrote = r.client_wrote_at ? `, and ${who} wrote again at ${clockIn(Date.parse(r.client_wrote_at), tz)}` : ''
  return { head: 'Promise late', late: span(nowMs - at), text: `It was due at ${clockIn(at, tz)}${wrote}.` }
}

// ── The CRM dock band and the menu ──────────────────────────────────────────

/** What the dock band lists: late, or due within the hour (the rest waits in Tasks), soonest first. */
export function bandRows(mine: TaskAlertRow[], nowMs: number): TaskAlertRow[] {
  return mine
    .filter((r) => r.state === 'late' || (r.due_at !== null && Date.parse(r.due_at) - nowMs <= 60 * 60_000))
    .sort((a, b) => Date.parse(a.due_at ?? '') - Date.parse(b.due_at ?? ''))
}

/** "2 follow-ups late. The first was due at 14:40." / "2 follow-ups. Next at 14:40, Priya." */
export function bandLine(rows: TaskAlertRow[], tz: string): { head: string; text: string; late: boolean } | null {
  if (rows.length === 0) return null
  const sorted = [...rows].sort((a, b) => Date.parse(a.due_at ?? '') - Date.parse(b.due_at ?? ''))
  const late = sorted.filter((r) => r.state === 'late')
  const n = (k: number) => `${k} follow-up${k === 1 ? '' : 's'}`
  if (late.length > 0) {
    return { head: `${n(late.length)} late.`, text: `The first was due at ${clockIn(Date.parse(late[0].due_at ?? ''), tz)}.`, late: true }
  }
  const next = sorted[0]
  const who = next.client ? `, ${next.client.split(' ')[0]}` : ''
  return { head: `${n(rows.length)}.`, text: `Next at ${clockIn(Date.parse(next.due_at ?? ''), tz)}${who}.`, late: false }
}

/** The menu's Tasks number: what's late. Yours; for whoever gives tasks out, every late one they see. */
export function menuCount(counts: TaskAlerts['counts'] | undefined, assigns: boolean): number {
  if (!counts) return 0
  return assigns ? (counts.team_late ?? counts.late) : counts.late
}

// ── Reminders ───────────────────────────────────────────────────────────────

export type Reminder = { key: string; title: string; body: string }

/** What to remind about now: 15 min before, at the due time, and (supervisors) 30 min after.
 *  `seen` holds keys already shown, so each fires once. */
export function dueReminders(mine: TaskAlertRow[], teamLate: TaskAlertRow[], nowMs: number, tz: string, seen: Set<string>): Reminder[] {
  const out: Reminder[] = []
  for (const r of mine) {
    if (!r.due_at) continue
    const at = Date.parse(r.due_at)
    const left = at - nowMs
    if (left > 0 && left <= 15 * 60_000 && !seen.has(`${r.id}:15`)) {
      out.push({ key: `${r.id}:15`, title: `Due at ${clockIn(at, tz)}: ${r.title}`, body: r.client ? `${r.client}${r.route ? ` · ${r.route}` : ''}` : '' })
    } else if (left <= 0 && left > -10 * 60_000 && !seen.has(`${r.id}:0`)) {
      out.push({ key: `${r.id}:0`, title: `Due now: ${r.title}`, body: r.client ? `${r.client}${r.route ? ` · ${r.route}` : ''}` : '' })
    }
  }
  for (const r of teamLate) {
    if (!seen.has(`${r.id}:30`)) {
      out.push({ key: `${r.id}:30`, title: `Late: ${r.title}`, body: `${r.assignee_name || 'Someone'} hasn’t marked it done.` })
    }
  }
  return out
}
