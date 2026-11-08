// What To read says — pinned on features/chats/to-read-view.ts.
// Runs with: node --experimental-strip-types --test tests/to-read-view.test.ts

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import type { Reading, ToReadList, ToReadRow } from '../src/lib/types.ts'
import {
  avatarText,
  clientName,
  eventText,
  groupNote,
  groupsOf,
  leadSentence,
  markerOf,
  nextUnread,
  readKey,
  rowLabel,
  rowLine,
  scopeNote,
  sideTitle,
  unreadCount,
  withMark,
} from '../src/features/chats/to-read-view.ts'

const NOW = new Date(2026, 10, 6, 15, 30).getTime() // Fri 6 Nov, 15:30 local

function row(over: Partial<ToReadRow> = {}): ToReadRow {
  return {
    id: 'c1', chat_number: 4810, kind: 'lost', at: new Date(2026, 10, 6, 14, 50).toISOString(), client: 'Olga V.',
    agent_id: 'rohan', agent_name: 'Rohan Mehta', route: 'New York → Dubai', status: 'closed',
    reasons: [{ key: 'back', at: new Date(2026, 10, 6, 14, 50).toISOString(), user_id: 'rohan', name: 'Rohan Mehta' }],
    ...over,
  }
}

function list(over: Partial<ToReadList> = {}): ToReadList {
  return {
    week_start: '2026-11-02T05:00:00+00:00',
    sample_week: { start: '2026-10-26T05:00:00+00:00', end: '2026-11-02T05:00:00+00:00' },
    lost: [
      row(),
      row({ id: 'c2', client: 'Hamid A.', reasons: [
        { key: 'anyone', at: new Date(2026, 10, 6, 14, 2).toISOString(), user_id: 'rohan', name: 'Rohan Mehta' },
        { key: 'back', at: new Date(2026, 10, 6, 14, 5).toISOString(), user_id: 'rohan', name: 'Rohan Mehta' },
      ], at: new Date(2026, 10, 6, 14, 2).toISOString() }),
      row({ id: 'c3', client: 'Rachel P.', agent_id: 'maria', agent_name: 'Maria Rotaru', at: new Date(2026, 10, 5, 18, 40).toISOString() }),
    ],
    sample: [
      row({ id: 's1', kind: 'sample', reasons: [], agent_id: 'ana', agent_name: 'Ana Ceban', at: new Date(2026, 9, 29, 16, 2).toISOString() }),
      row({ id: 's2', kind: 'help', reasons: [], at: new Date(2026, 9, 28, 13, 10).toISOString() }),
    ],
    people: [{ id: 'ana', name: 'Ana Ceban' }, { id: 'maria', name: 'Maria Rotaru' }, { id: 'rohan', name: 'Rohan Mehta' }],
    no_team: false,
    ...over,
  }
}

test('today, earlier this week, and the sample; one person at a time', () => {
  const g = groupsOf(list(), 'all', NOW)
  assert.deepEqual(g.today.map((r) => r.id), ['c1', 'c2'])
  assert.deepEqual(g.earlier.map((r) => r.id), ['c3'])
  assert.deepEqual(g.sample.map((r) => r.id), ['s1', 's2'])
  const rohan = groupsOf(list(), 'rohan', NOW)
  assert.deepEqual([...rohan.today, ...rohan.earlier, ...rohan.sample].map((r) => r.id), ['c1', 'c2', 's2'])
})

test('read is per week and per list; the count and the next one follow it', () => {
  const l = list()
  assert.equal(readKey(l, row()), '2026-11-02:lost:c1')
  assert.equal(readKey(l, row({ id: 's1', kind: 'sample' })), '2026-11-02:sample:s1')
  const read = new Set([readKey(l, row())])
  assert.equal(unreadCount(l, read), 4)
  assert.equal(unreadCount(undefined, read), 0)
  const ordered = [...l.lost, ...l.sample]
  assert.equal(nextUnread(ordered, l, read, 'c1')?.id, 'c2')
  assert.equal(nextUnread(ordered, l, new Set(ordered.map((r) => readKey(l, r))), 'c1'), null)
  assert.equal(groupNote(l.lost, l, read, 'today'), '2 not read · today')
  assert.equal(groupNote([row()], l, read), 'all read')
})

test('a row in words: whose, where, and the rules it broke', () => {
  assert.equal(rowLine(row()), 'Rohan Mehta · New York → Dubai')
  assert.equal(rowLine(row({ agent_name: null, route: null })), 'Nobody')
  const two = rowLabel(list().lost[1])
  assert.deepEqual([two.text, two.more, two.tone], ['“Anyone there?”', '+ Went back to the assistant', 'bad'])
  assert.deepEqual(rowLabel(row({ kind: 'sample', reasons: [] })), { text: 'Picked at random', more: null, tone: 'muted' })
  assert.equal(rowLabel(row({ kind: 'help', reasons: [] })).text, 'One more: needs help')
  assert.equal(rowLabel(row({ reasons: [{ key: 'yes', at: '', user_id: null, name: null }] })).tone, 'warn')
})

test('a supervisor’s masked client reads as the chat number', () => {
  assert.equal(avatarText('•••cu'), '#')
  assert.equal(avatarText('Olga V.'), 'OV')
  assert.equal(clientName('•••cu', 4810), '#4810 · •••cu')
  assert.equal(clientName('Olga V.', 4810), 'Olga V.')
  assert.equal(clientName('', null), 'A client')
})

test('the side panel says why it is here, with no AI', () => {
  const lost = sideTitle(list().lost[1])
  assert.equal(lost.kicker, 'Lost · Rohan Mehta')
  assert.equal(lost.title, 'The client asked if anyone was there.')
  assert.equal(lost.why, 'In “Lost” because the client asked if anyone was there and the chat went back to the assistant. These come from fixed rules, no AI.')
  assert.equal(sideTitle(row({ kind: 'sample', agent_name: 'Ana Ceban', reasons: [] })).title, 'A normal chat of Ana’s, picked at random.')
  assert.match(sideTitle(row({ kind: 'help', reasons: [] })).why, /first reply over 60 s/)
  assert.equal(sideTitle(row({ reasons: [{ key: 'never', at: '', user_id: 'ana', name: 'Ana Ceban' }] })).title, 'Given to Ana, but nobody wrote.')
})

test('what happened, and the markers in the chat', () => {
  assert.deepEqual(eventText({ at: '', kind: 'first_reply', seconds: 41 }, 'Olga V.'), { text: 'First reply · 41 s', bad: false })
  assert.equal(eventText({ at: '', kind: 'first_reply', seconds: 74 }, 'Olga V.').bad, true)
  assert.deepEqual(eventText({ at: '', kind: 'went_back' }, 'Olga V.'), { text: 'No reply for 90 s → back to the assistant', bad: true })
  assert.equal(eventText({ at: '', kind: 'started' }, 'Olga V.').text, 'Olga V. started the chat')
  assert.equal(eventText({ at: '', kind: 'not_ready', name: 'Rohan Mehta' }, '').text, 'Rohan Mehta went Not ready')
  assert.deepEqual(markerOf('back', 'Rohan Mehta'), { title: 'Here the chat went back to the assistant.', text: 'Rohan didn’t reply for 90 s.', tone: 'bad' })
  assert.equal(markerOf('yes', 'Victor Rusu').tone, 'warn')
})

test('the lead in one sentence', () => {
  const lead: NonNullable<Reading['lead']> = {
    id: 'L1', route: 'New York → Dubai', origin_code: 'JFK', destination_code: 'DXB', departure_date: '2026-11-20',
    return_date: '2026-11-27', passengers: 1, trip_type: 'round_trip', cabin_class: 'business', level: 'almost',
    crm_state: 'in_crm', crm_lead_id: '208131', crm_push_gate_reason: null, phone: '•••3377', email: 'ol•••@example.com',
  }
  assert.equal(leadSentence(lead), 'New York → Dubai, 20 Nov – 27 Nov, 1 traveler. Almost ready. In the CRM · lead 208131.')
  assert.equal(leadSentence({ ...lead, route: null, departure_date: null, return_date: null, passengers: null, level: 'contact', crm_state: 'no_phone' }),
    'No route yet. Contact only. No phone, so not in the CRM.')
})

test('the line under the list comes from the reader’s role, not from the rows', () => {
  const team = 'Your team only. Phone and e-mail are hidden.'
  // A supervisor whose team lost nothing and has no sample is still team-only.
  assert.equal(scopeNote({ no_team: false }, true), team)
  assert.equal(scopeNote(undefined, true), team)
  assert.equal(scopeNote({ no_team: false }, false), 'Every person’s lost chats and last week’s sample.')
  assert.match(scopeNote({ no_team: true }, true), /^You’re not in a team yet/)
  const src = (p: string) => readFileSync(new URL(`../src/features/chats/${p}`, import.meta.url), 'utf8')
  assert.match(src('index.tsx'), /scoped=\{role === 'supervisor'\}/)
  assert.match(src('to-read.tsx'), /const scopeLine = scopeNote\(list, scoped\)/)
  assert.ok(!/concat\(list\.sample\)\.some/.test(src('to-read.tsx')), 'the line still guesses from masked names')
})

test('Mark read keeps two weeks, and two marks in a row are both kept', () => {
  const a = withMark(new Set(), '2026-11-02:c1:lost')
  const b = withMark(a, '2026-11-02:c2:lost')
  assert.deepEqual([...b].sort(), ['2026-11-02:c1:lost', '2026-11-02:c2:lost'])
  assert.equal(a.size, 1, 'the set it was given is not changed')
  // Marking in the week of 2 Nov keeps 19 Oct (14 days) and drops 12 Oct.
  const old = new Set(['2026-10-12:x:lost', '2026-10-19:y:sample'])
  assert.deepEqual([...withMark(old, '2026-11-02:c1:lost')].sort(), ['2026-10-19:y:sample', '2026-11-02:c1:lost'])
  // The updater only computes; storage follows the state.
  const state = src2('to-read-state.ts')
  const updater = state.slice(state.indexOf('const mark ='), state.indexOf('useEffect('))
  assert.match(updater, /setRead\(\(s\) => withMark\(s, key\)\)/)
  assert.ok(!updater.includes('localStorage'))
  assert.match(state, /useEffect\(\(\) => \{\s*try \{\s*localStorage\.setItem\(READ_STORE, JSON\.stringify\(\[\.\.\.read\]\)\)/)
})

function src2(p: string): string {
  return readFileSync(new URL(`../src/features/chats/${p}`, import.meta.url), 'utf8')
}
