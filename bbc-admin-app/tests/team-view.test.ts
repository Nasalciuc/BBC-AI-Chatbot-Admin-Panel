// What Team says — pinned on features/teams/view.ts.
// Runs with: node --experimental-strip-types --test tests/team-view.test.ts

import { test } from 'node:test'
import assert from 'node:assert/strict'
import type { PersonDay, TeamNow } from '../src/lib/types.ts'
import {
  activityText,
  attentionLine,
  canSeeTeam,
  coverageCell,
  coverageNote,
  dayTiles,
  howChatsReachPeople,
  isProblem,
  minutesText,
  presenceLine,
  readyBars,
  rosterStatus,
  shiftText,
  statCards,
  timer,
  timerTone,
  withPersonLine,
} from '../src/features/teams/view.ts'

const NOW = Date.parse('2026-11-04T12:12:00Z')
const ago = (s: number) => new Date(NOW - s * 1000).toISOString()

test('who opens Team', () => {
  for (const r of ['owner', 'admin', 'dev', 'supervisor', 'project_manager']) assert.ok(canSeeTeam(r), r)
  for (const r of ['sales', 'support', 'qa', undefined]) assert.ok(!canSeeTeam(r), String(r))
})

test('live timers and their colour', () => {
  assert.equal(timer(ago(51), NOW), '0:51')
  assert.equal(timer(ago(191), NOW), '3:11')
  assert.equal(timer(ago(34 * 60), NOW), '34 min')
  assert.equal(timerTone(ago(51), NOW), 'ok')
  assert.equal(timerTone(ago(131), NOW), 'warn')
  assert.equal(timerTone(ago(301), NOW), 'bad')
  assert.ok(isProblem(ago(130), NOW, true))
  assert.ok(!isProblem(ago(130), NOW, false), 'waiting for the client is not a problem')
  assert.equal(minutesText(122), '2 h 02 m')
})

const NOW_DATA: TeamNow = {
  no_team: false, now: new Date(NOW).toISOString(),
  stats: { ready: 3, no_signal: 1, online: 6, waiting: 2, oldest_waiting: { client: 'Grace M.', since: ago(540) }, first_reply_today_s: 52, replies_today: 9, back_today: 2 },
}

test('the four cards', () => {
  const cards = statCards(NOW_DATA)
  assert.deepEqual(cards.map((c) => `${c.value} ${c.unit}`), ['3 of 6 online', '2 chats', '52 s median', '2 chats'])
  assert.equal(cards[0].sub, '1 more is Ready but sends no signal')
  assert.equal(cards[1].sub, 'Oldest: Grace M., 9:00')
  assert.equal(cards[1].tone, 'bad')
  const quiet = statCards({ ...NOW_DATA, stats: { ...NOW_DATA.stats!, no_signal: 0, waiting: 0, oldest_waiting: null, first_reply_today_s: null, replies_today: 0, back_today: 0 } })
  assert.deepEqual(quiet.map((c) => c.sub), ['Everyone Ready is in the queue', 'Nobody in the queue', 'No first reply yet today', 'No first reply within 90 s'])
})

test('needs attention in words, without he or she', () => {
  assert.equal(
    attentionLine({ kind: 'client_waiting', level: 'urgent', user_id: 'r', name: 'Rohan Mehta', conversation_id: 'c', client: 'Sophia R.', chat_number: 1, minutes: 34 }).text,
    'No message in Sophia R.’s chat for 34 min.',
  )
  const ns = attentionLine({ kind: 'no_signal', level: 'check', user_id: 'a', name: 'Ana Ceban', minutes: 4, off_after_minutes: 10 })
  assert.equal(ns.text, 'Ready, but the panel has sent no signal for 4 min, so they’re out of the queue. Ready switches off at 10 min.')
  assert.doesNotMatch(ns.text, /\b(he|she|his|her)\b/)
  const q = attentionLine({ kind: 'queue_wait', level: 'check', tunnel: 'sales', conversation_id: 'c', client: 'Grace M.', chat_number: 1, minutes: 9, ready: 2, ready_chats: [2, 2] })
  assert.equal(`${q.who}. ${q.text}`, 'Sales queue. Grace M. has waited 9 min. The 2 people Ready have 2 chats each.')
  const q3 = attentionLine({ kind: 'queue_wait', level: 'check', tunnel: 'sales', conversation_id: 'c', client: 'Grace M.', chat_number: 1, minutes: 9, ready: 3, ready_chats: [2, 2, 1] })
  assert.match(q3.text, /The 3 people Ready have 2, 2 and 1 chats\.$/)
  const q0 = attentionLine({ kind: 'queue_wait', level: 'check', tunnel: 'support', conversation_id: 'c', client: '', chat_number: 1, minutes: 4, ready: 0, ready_chats: [] })
  assert.equal(`${q0.who}. ${q0.text}`, 'Support queue. A client has waited 4 min. Nobody is Ready.')
  assert.equal(attentionLine({ kind: 'coverage', level: 'check', tunnel: 'support', user_id: 'p', name: 'Priya Nair', since: null }).text, 'Only 1 person Ready (Priya Nair).')
  const wb = attentionLine({ kind: 'went_back', level: 'info', user_id: 'i', name: 'Ion Ciobanu', at: '2026-11-04T08:48:00Z', conversation_id: 'c', presence: 'offline', last_seen_at: '2026-11-04T09:20:00Z' })
  assert.match(wb.text, /^A chat went back to the assistant at \d\d:48: no first reply within 90 s\. Offline since \d\d:20\.$/)
})

test('a card with a person says who is waiting for whom', () => {
  assert.deepEqual(withPersonLine({ client: 'Sophia R.', client_waiting: true, since: ago(34 * 60), agent_name: 'Rohan Mehta' }, NOW), {
    text: 'Sophia wrote 34 min ago · no reply from Rohan yet', tone: 'bad',
  })
  assert.deepEqual(withPersonLine({ client: 'Mia J.', client_waiting: false, since: ago(421), agent_name: 'Daniel Popescu' }, NOW), {
    text: 'Daniel replied 7:01 ago · waiting for Mia', tone: 'muted',
  })
  assert.equal(withPersonLine({ client: '•••es', client_waiting: true, since: ago(30), agent_name: 'Victor Rusu' }, NOW).text, 'The client wrote 0:30 ago · no reply from Victor yet')
})

test('teams: coverage, shift, roster status', () => {
  assert.equal(coverageCell(null), 'bg-k-f7f7f7')
  assert.equal(coverageCell(0), 'bg-k-ececec')
  assert.equal(coverageCell(3), 'bg-k-00a645')
  assert.equal(coverageNote([null, null]), 'The day hasn’t started')
  assert.equal(coverageNote([1, 1, 1, 2, null]), 'Only 1 person Ready for 3 h')
  assert.equal(coverageNote([2, 2, 3]), 'Covered so far')
  assert.equal(shiftText('12:00:00', '20:00:00'), 'shift 12:00–20:00')
  assert.equal(shiftText(null, null), 'no shift set')
  assert.equal(rosterStatus({ is_active: false, role: 'sales', last_seen_at: null }, undefined).label, 'Deactivated')
  assert.equal(rosterStatus({ is_active: true, role: 'qa', last_seen_at: null }, undefined).label, 'Active')
})

const DAY: PersonDay = {
  user: { id: 'rohan', name: 'Rohan Mehta', email: 'r@x', role: 'sales', tunnel: 'sales', is_active: true, team: null, presence: 'ready', ready_since: '2026-11-04T11:08:00Z', last_seen_at: ago(5) },
  today: { chats_now: 2, took: 6, first_reply_median_s: 92, replied_to: 5, went_back: 1, takes_lost: 0, minutes_online: 122, first_seen_at: '2026-11-04T10:05:00Z' },
  ready_today: [['2026-11-04T08:00:00Z', '2026-11-04T09:00:00Z'], ['2026-11-04T11:08:00Z', null]],
  week_online: [],
  activity: [],
  day_start: '2026-11-03T22:00:00Z',
}

test('one person’s day', () => {
  const tiles = dayTiles(DAY)
  assert.deepEqual(tiles.map((t) => `${t.value} ${t.label}`).slice(0, 4), ['2 chats now', '6 taken today', '92 s first reply', '2 h 02 m online'])
  assert.equal(tiles[2].tone, 'warn')
  assert.equal(tiles[6].tone, 'bad')
  const bars = readyBars(DAY, NOW)
  assert.equal(bars.length, 2)
  assert.equal(Math.round(bars[0].left), Math.round((10 / 24) * 100))
  assert.equal(Math.round(bars[0].width * 10) / 10, Math.round((1 / 24) * 1000) / 10)
  assert.match(presenceLine(DAY.user).text, /^Ready since \d\d:08$/)
  assert.equal(presenceLine({ ...DAY.user, presence: 'no_signal' }).tone, 'text-k-8a4b00')
  assert.equal(activityText({ at: '', kind: 'replied', client: 'James W.', seconds: 75 }, 'Rohan').text, 'Replied to James W. in 75 s')
  assert.equal(activityText({ at: '', kind: 'client_waiting', client: 'Sophia R.' }, 'Rohan').text, 'Sophia R. wrote; no message from Rohan since')
  assert.equal(activityText({ at: '', kind: 'take_lost', client: 'Ben C.' }, 'Rohan').text, 'Pressed Take on Ben C.’s chat; someone else got it first')
})

test('how chats reach people uses the server’s windows', () => {
  const lines = howChatsReachPeople({ queue_s: 90, ready_off_s: 600 })
  assert.equal(lines.length, 5)
  assert.match(lines[3], /within 90 seconds\. Ready switches off after 10 minutes/)
})
