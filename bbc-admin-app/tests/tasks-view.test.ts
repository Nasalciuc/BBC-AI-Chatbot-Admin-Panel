// Tasks — the times and sentences the page writes.
// Runs with: node --experimental-strip-types --test tests/tasks-view.test.ts

import { test } from 'node:test'
import assert from 'node:assert/strict'
import type { TaskAlertRow, TaskRow } from '../src/lib/types.ts'
import {
  bandLine,
  bandRows,
  cameBackMessage,
  caughtLine,
  clientLine,
  clientTime,
  countStates,
  dueMain,
  dueReminders,
  dueSub,
  groupTasks,
  lateBanner,
  menuCount,
  shownState,
  newTaskChoices,
  pickedInstant,
  snoozeChoices,
  span,
  summaryLine,
  unownedNote,
  wroteAgain,
  zonedInstant,
  zoneCity,
} from '../src/features/tasks/view.ts'

const NY = 'America/New_York'
// Friday 6 Nov 2026, 15:20 in New York (EST, UTC-5).
const NOW = Date.parse('2026-11-06T20:20:00Z')
const at = (iso: string) => new Date(Date.parse(iso)).toISOString()

function row(over: Partial<TaskRow> = {}): TaskRow {
  return {
    id: 't1', number: 1, title: 'Send Michael three options for New York → London', kind: 'email', kind_label: 'E-mail',
    source: 'caught', source_label: 'Caught in chat', quote: 'I’ll send you three options within the hour.',
    conversation_id: 'c1', message_id: 'm1', lead_id: 'l1', chat_number: 4810, client: 'Michael R.', route: 'New York → London',
    departure_date: '2026-11-16', passengers: 2, children_count: 0, infant_count: 0, client_city: 'New York', client_zone: NY,
    due_at: at('2026-11-06T20:07:00Z'), due_kind: 'said', state: 'late', assignee_id: 'victor', assignee_name: 'Victor Rusu',
    unowned_reason: null, unowned_from_name: null, unowned_at: null, created_by_name: null,
    client_wrote_at: at('2026-11-06T20:02:00Z'), created_at: at('2026-11-06T19:07:00Z'), done_at: null, done_by_name: null,
    ...over,
  }
}

test('zone arithmetic: a wall time in New York is the right instant', () => {
  assert.equal(new Date(zonedInstant(2026, 11, 6, 14, 7, NY)).toISOString(), '2026-11-06T19:07:00.000Z')
  // Summer time (EDT, UTC-4).
  assert.equal(new Date(zonedInstant(2026, 7, 1, 10, 0, NY)).toISOString(), '2026-07-01T14:00:00.000Z')
  assert.equal(zoneCity(NY), 'New York')
  assert.equal(pickedInstant('2026-11-10', '09:30', NY), '2026-11-10T14:30:00.000Z')
})

test('due times: today a clock, another day with its day, a day with no time', () => {
  assert.equal(dueMain(row(), NOW, NY), '15:07')
  assert.equal(dueMain(row({ due_at: at('2026-11-09T15:00:00Z') }), NOW, NY), 'Mon 9 Nov, 10:00')
  assert.equal(dueMain(row({ due_at: at('2026-11-11T04:00:00Z'), due_kind: 'day' }), NOW, NY), 'Tue 10 Nov') // by 23:00 New York
  assert.equal(dueMain(row({ due_at: null }), NOW, NY), 'No time set')
  assert.equal(span(13 * 60_000), '13 min')
  assert.equal(span(160 * 60_000), '2 h 40 min')
})

test('the client’s time names the city it comes from', () => {
  assert.equal(clientTime(row(), NY), 'New York time too')
  const priya = row({ due_at: at('2026-11-06T19:40:00Z'), client_city: 'Chicago', client_zone: 'America/Chicago' })
  assert.equal(clientTime(priya, NY), '13:40 in Chicago')
  // Seattle at 9:00 New York is 6:00 the same day; Sydney is the next day.
  const tom = row({ due_at: at('2026-11-07T14:00:00Z'), client_city: 'Seattle', client_zone: 'America/Los_Angeles' })
  assert.equal(clientTime(tom, NY), '06:00 in Seattle')
  const syd = row({ due_at: at('2026-11-06T22:00:00Z'), client_city: 'Sydney', client_zone: 'Australia/Sydney' })
  assert.equal(clientTime(syd, NY), 'Sat 09:00 in Sydney')
  assert.equal(clientTime(row({ client_city: null }), NY), null)
})

test('Today and Later follow the viewer\'s day, like the times on the page', () => {
  // 20:00 in New York is 03:00 the next day in Chisinau.
  const now = Date.parse('2026-11-07T01:00:00Z') // Fri 20:00 New York, Sat 03:00 Chisinau
  const eod = row({ state: 'today', due_at: at('2026-11-07T04:00:00Z') }) // 23:00 New York = 06:00 Chisinau
  assert.equal(shownState(eod, now, NY), 'today')
  assert.equal(shownState(eod, now, 'Europe/Chisinau'), 'today')
  const tomorrowHere = row({ state: 'today', due_at: at('2026-11-07T23:30:00Z') }) // Sat 18:30 NY, Sun 01:30 Chisinau
  assert.equal(shownState(tomorrowHere, now, 'Europe/Chisinau'), 'later')
  assert.equal(shownState(row({ state: 'today', due_at: at('2026-11-07T00:59:00Z') }), now, NY), 'late')
  assert.equal(shownState(row({ state: 'done' }), now, NY), 'done')
  assert.deepEqual(countStates([row({ state: 'late' }), row({ state: 'today', assignee_id: null }), row({ state: 'later' })]),
    { late: 1, today: 1, later: 1, unowned: 1 })
})

test('the line under the due time', () => {
  assert.equal(dueSub(row(), NOW, NY), '13 min late · New York time too')
  const later = row({ state: 'today', due_at: at('2026-11-06T23:00:00Z'), due_kind: 'default', client_city: null })
  assert.equal(dueSub(later, NOW, NY), 'in 2 h 40 min · no time said')
  // "Today" ends on the business clock: the client's is named only when it reads differently from yours.
  const eod = row({ state: 'today', due_at: at('2026-11-07T04:00:00Z'), due_kind: 'end_of_day', client_city: 'New York', client_zone: NY })
  assert.equal(dueSub(eod, NOW, NY), 'in 7 h 40 min · “today” = end of the working day')
  assert.equal(dueSub(eod, NOW, 'Europe/Chisinau'), 'in 7 h 40 min · “today” = end of the working day · Fri 23:00 in New York')
  // A day with no time: 23:00 in the maker's clock. NOW is Fri 6 Nov.
  const day = row({ state: 'later', due_kind: 'day', due_at: at('2026-11-11T04:00:00Z') }) // Tue 23:00 New York
  assert.equal(dueMain(day, NOW, NY), 'Tue 10 Nov')
  assert.equal(dueSub(day, NOW, NY), 'no time set')
  // Seen from another zone it's the deadline it really is there, not "Wed 11 Nov" (a day too late).
  assert.equal(dueMain(day, NOW, 'Europe/Chisinau'), 'Wed 11 Nov, 06:00')
  assert.equal(dueSub(day, NOW, 'Europe/Chisinau'), '')
})

test('"came back to the chat" waits for a pause after the promise', () => {
  const made = Date.parse('2026-11-06T19:07:00Z')
  const msgs = [
    { id: 'p', role: 'agent', created_at: '2026-11-06T19:07:00Z' },
    { id: 'thanks', role: 'user', created_at: '2026-11-06T19:08:00Z' },
    { id: 'news', role: 'user', created_at: '2026-11-06T20:02:00Z' },
  ]
  assert.equal(cameBackMessage(msgs, made), 'news')
  assert.equal(cameBackMessage(msgs.slice(0, 2), made), null)
  assert.equal(cameBackMessage(msgs, null), null)
})

test('client line, Unowned and "wrote again"', () => {
  assert.equal(clientLine(row()), 'Michael R. · New York → London · 16 Nov · 2 travelers')
  assert.equal(clientLine(row({ client: null, departure_date: null, passengers: null })), 'New York → London')
  assert.equal(unownedNote(row({ unowned_reason: 'left_shift', unowned_from_name: 'Rohan Mehta', unowned_at: at('2026-11-06T19:52:00Z') }), NY),
    'Rohan Mehta went Not ready at 14:52')
  assert.equal(unownedNote(row({ unowned_reason: 'ai_only' }), NY), 'Asked in a chat with the AI')
  assert.equal(wroteAgain(row(), NY), 'Michael wrote again 15:02')
})

test('summary and groups', () => {
  assert.equal(summaryLine({ counts: { late: 2, today: 2, later: 2, unowned: 0 }, on_time: { on_time: 12, due: 14 } }),
    '2 late · 2 today · 2 later · 86% on time this week (12 of 14)')
  assert.equal(summaryLine({ counts: { late: 0, today: 1, later: 0, unowned: 0 }, on_time: { on_time: 0, due: 0 } }), '0 late · 1 today · 0 later')
  const rows = [row({ id: 'u', assignee_id: null, state: 'today' }), row({ id: 'l' }), row({ id: 't', state: 'today' }), row({ id: 'x', state: 'later' })]
  assert.deepEqual(groupTasks(rows, true).map((g) => [g.key, g.rows.map((r) => r.id)]),
    [['unowned', ['u']], ['late', ['l']], ['today', ['t']], ['later', ['x']]])
  assert.deepEqual(groupTasks(rows, false).map((g) => g.key), ['late', 'today', 'later'])
})

test('new due times: +1 hour, tomorrow and Monday at 10:00', () => {
  const choices = snoozeChoices(NOW, NY)
  assert.deepEqual(choices.map((c) => c.label), ['+1 hour', 'Tomorrow 10:00', 'Monday 10:00'])
  assert.equal(new Date(choices[1].at).toISOString(), '2026-11-07T15:00:00.000Z')
  assert.equal(new Date(choices[2].at).toISOString(), '2026-11-09T15:00:00.000Z')
  // On a Sunday, Monday is tomorrow: no second Monday chip.
  const sunday = Date.parse('2026-11-08T15:00:00Z')
  assert.deepEqual(snoozeChoices(sunday, NY).map((c) => c.label), ['+1 hour', 'Tomorrow 10:00'])
  assert.deepEqual(newTaskChoices(NOW, NY).map((c) => c.label), ['In 2 hours', 'Today 17:00', 'Tomorrow 10:00'])
  // At 16:40 "Today 17:00" is too close to offer.
  assert.deepEqual(newTaskChoices(Date.parse('2026-11-06T21:40:00Z'), NY).map((c) => c.label), ['In 2 hours', 'Tomorrow 10:00'])
})

test('in the chat: caught, late, done and the banner', () => {
  assert.deepEqual(caughtLine(row({ state: 'today' }), NOW, NY), { head: 'Follow-up caught:', text: 'e-mail by 15:07', late: false, done: false })
  assert.deepEqual(caughtLine(row(), NOW, NY), { head: 'Follow-up late:', text: 'e-mail was due 15:07', late: true, done: false })
  assert.equal(caughtLine(row({ kind: 'todo', state: 'today' }), NOW, NY).text, 'follow-up by 15:07')
  assert.deepEqual(lateBanner(row(), NOW, NY), { head: 'Promise late', late: '13 min', text: 'It was due at 15:07, and Michael wrote again at 15:02.' })
})

const alert = (over: Partial<TaskAlertRow>): TaskAlertRow => ({
  id: 'a', title: 'Call Priya to confirm the seats', kind: 'call', due_at: at('2026-11-06T19:40:00Z'), state: 'late',
  conversation_id: 'c', client: 'Priya S.', route: 'Chicago → Delhi', assignee_name: null, ...over,
})

test('the dock band lists what is late or due within the hour, soonest first', () => {
  const now = Date.parse('2026-11-06T20:00:00Z')
  const rows = [
    alert({ id: 'later', state: 'today', due_at: at('2026-11-06T22:00:00Z') }),
    alert({ id: 'soon', state: 'today', due_at: at('2026-11-06T20:45:00Z') }),
    alert({ id: 'late', state: 'late', due_at: at('2026-11-06T19:40:00Z') }),
  ]
  assert.deepEqual(bandRows(rows, now).map((r) => r.id), ['late', 'soon'])
  assert.deepEqual(bandRows([rows[0]], now), [])
})

test('the CRM dock band and the menu count', () => {
  assert.equal(bandLine([], NY), null)
  assert.deepEqual(bandLine([alert({}), alert({ id: 'b', due_at: at('2026-11-06T20:07:00Z') })], NY),
    { head: '2 follow-ups late.', text: 'The first was due at 14:40.', late: true })
  assert.deepEqual(bandLine([alert({ state: 'today', due_at: at('2026-11-06T21:00:00Z') })], NY),
    { head: '1 follow-up.', text: 'Next at 16:00, Priya.', late: false })
  // The canvas's number: what's late (an agent's own; a supervisor's whole view, Unowned too).
  assert.equal(menuCount({ late: 2, today: 1, unowned: 3, team_late: 4 }, false), 2)
  assert.equal(menuCount({ late: 2, today: 1, unowned: 3, team_late: 4 }, true), 4)
  assert.equal(menuCount({ late: 2, today: 1, unowned: 3 }, true), 2) // an API from before team_late
  assert.equal(menuCount(undefined, true), 0)
})

test('reminders: 15 min before, at the due time, supervisors 30 min after — each once', () => {
  const soon = alert({ id: 's', state: 'today', due_at: new Date(NOW + 10 * 60_000).toISOString() })
  const now = alert({ id: 'n', state: 'late', due_at: new Date(NOW - 60_000).toISOString() })
  const far = alert({ id: 'f', state: 'today', due_at: new Date(NOW + 60 * 60_000).toISOString() })
  const team = alert({ id: 'x', assignee_name: 'Rohan Mehta' })
  const first = dueReminders([soon, now, far], [team], NOW, NY, new Set())
  assert.deepEqual(first.map((r) => r.key), ['s:15', 'n:0', 'x:30'])
  assert.equal(first[2].body, 'Rohan Mehta hasn’t marked it done.')
  assert.deepEqual(dueReminders([soon, now], [team], NOW, NY, new Set(first.map((r) => r.key))), [])
})

// ── Wiring: where Tasks shows up outside its page ────────────────────────────

test('the chat shows the caught line, the late banner only on your own chat, and refreshes after a send', async () => {
  const { readFileSync } = await import('node:fs')
  const src = (p: string) => readFileSync(new URL(`../src/${p}`, import.meta.url), 'utf8')
  const detail = src('features/chats/detail.tsx')
  assert.match(detail, /const task = chatTasks\.byMessage\.get\(msg\.id\)/)
  assert.match(detail, /\{canWrite && chatTasks\.lateMine && \(\s*<LateBanner/)
  assert.match(detail, /await sendAgentMessage\(sentFor, input\.trim\(\)\)[\s\S]{0,700}chatTasks\.refresh\(\)/)
  // "Not a promise" only for a caught task, by its owner or a manager.
  assert.match(detail, /task\.source === 'caught' && \(task\.assignee_id === me\?\.accountNo \|\| isAdmin\)/)
  // The band sits over the list only inside the CRM dock (no menu there).
  assert.match(src('features/chats/index.tsx'), /\{embedded && <TasksBand \/>\}/)
})

test('Tasks is in the menu for everyone but QA, with its count', async () => {
  const { readFileSync } = await import('node:fs')
  const src = (p: string) => readFileSync(new URL(`../src/${p}`, import.meta.url), 'utf8')
  assert.match(src('components/layout/data/sidebar-data.ts'), /\.\.\.\(role !== 'qa'\s*\? \[\{ key: 'tasks' as const, title: 'Tasks', url: '\/tasks'/)
  assert.match(src('components/layout/app-sidebar.tsx'), /item\.key === 'tasks' && taskCount > 0/)
  assert.match(src('components/layout/mobile-tab-bar.tsx'), /<Link to='\/tasks'/)
  // Reminders run once per person: the dozing tab stays quiet.
  assert.match(src('features/tasks/use-task-alerts.ts'), /if \(dormant \|\| !data\?\.set_up \|\| !data\.time_zone\) return/)
  assert.match(src('components/layout/authenticated-layout.tsx'), /useTaskReminders\(\)/)
})

test('the template is gone: no fake bulk actions, no fake import', async () => {
  const { existsSync } = await import('node:fs')
  for (const f of ['components/data-table-bulk-actions.tsx', 'components/tasks-import-dialog.tsx', 'data/tasks.ts']) {
    assert.ok(!existsSync(new URL(`../src/features/tasks/${f}`, import.meta.url)), f)
  }
})

test('Make a task counts its due choices from the click, not from the last poll', async () => {
  const { readFileSync } = await import('node:fs')
  const src = readFileSync(new URL('../src/features/tasks/components/make-task.tsx', import.meta.url), 'utf8')
  // A dozing tab stops polling: the bell's dataUpdatedAt can be hours old.
  assert.doesNotMatch(src, /dataUpdatedAt/)
  assert.match(src, /onClick=\{\(\) => \{\s*setOpenedAt\(Date\.now\(\)\)\s*setOpen\(true\)/)
  assert.match(src, /nowMs=\{openedAt\}/)
})
