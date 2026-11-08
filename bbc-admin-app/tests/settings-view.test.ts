// What Settings says — pinned on features/settings/view.ts.
// Runs with: node --experimental-strip-types --test tests/settings-view.test.ts

import { test } from 'node:test'
import assert from 'node:assert/strict'
import type { MyAccess } from '../src/lib/types.ts'
import {
  accessRows,
  askLine,
  nameError,
  phoneError,
  photoError,
  roleChangedNote,
  sessionText,
  teamLine,
  whenText,
} from '../src/features/settings/view.ts'

function me(over: Partial<MyAccess> = {}): MyAccess {
  return {
    role: 'sales', tunnel_scope: 'sales', is_active: true, chat_enabled: true, receives_chats: true,
    team: { id: 't1', name: 'Day shift', shift_name: 'Day shift', shift_start: '12:00:00', shift_end: '20:00:00', supervisor_name: 'Victor Rusu' },
    leads_teams: [], session_role: 'sales', ...over,
  }
}

const row = (a: MyAccess, label: string) => accessRows(a).find((r) => r.label === label)?.value

test('your access in four rows, from the database', () => {
  assert.deepEqual(accessRows(me()).map((r) => [r.label, r.value]), [
    ['Role', 'Sales agent'],
    ['Chats', 'Sales only'],
    ['Team', 'Day shift · 12:00–20:00 · Victor Rusu (supervisor)'],
    ['Receives chats', 'Yes'],
  ])
  assert.match(accessRows(me())[0].note ?? '', /Answers sales chats/)
  assert.equal(row(me({ team: null }), 'Team'), 'Not in a team')
  assert.equal(row(me({ tunnel_scope: 'all', role: 'qa', receives_chats: false, team: null }), 'Chats'), 'Sales and support · reads, never answers')
})

test('why no chats come, in the reason the server has', () => {
  assert.equal(row(me({ receives_chats: false, chat_enabled: false }), 'Receives chats'), 'No: an owner or admin switched your chats off')
  assert.equal(row(me({ receives_chats: false, is_active: false }), 'Receives chats'), 'No: your account is turned off')
  assert.match(row(me({ role: 'owner', receives_chats: false }), 'Receives chats') ?? '', /You can still take one yourself/)
  assert.equal(row(me({ role: 'supervisor', receives_chats: false }), 'Receives chats'), 'No: your role reads chats and doesn’t answer them')
})

test('a supervisor’s team is the one they lead', () => {
  const t = { id: 't1', name: 'Test group', shift_name: null, shift_start: null, shift_end: null, supervisor_name: null }
  assert.equal(row(me({ role: 'supervisor', team: null, leads_teams: [t] }), 'Team'), 'Leads Test group')
  assert.match(row(me({ role: 'supervisor', team: null }), 'Team') ?? '', /^No team yet/)
  assert.equal(row(me({ role: 'project_manager', team: null }), 'Team'), 'No teams yet')
  assert.equal(teamLine({ ...t, shift_name: 'Evenings', shift_start: '20:00:00', shift_end: '04:00:00', supervisor_name: 'Ana' }), 'Test group · Evenings · 20:00–04:00 · Ana (supervisor)')
})

test('who to ask, and a role that changed since you signed in', () => {
  assert.equal(askLine(me()), 'Something here is wrong? Ask Victor Rusu, your supervisor, or an admin.')
  assert.equal(askLine(me({ team: null })), 'Something here is wrong? Ask an owner or an admin.')
  assert.equal(askLine(me({ role: 'owner' })), 'Something here is wrong? Ask another owner.')
  assert.equal(roleChangedNote(me()), null)
  assert.equal(roleChangedNote(me({ session_role: null })), null)
  assert.equal(roleChangedNote(me({ role: 'support', session_role: 'sales' })), 'Your role is now Support agent. This session still works as Sales agent until you sign in again.')
})

test('the profile checks match the server: name 2–100, phone with the country code, photo 2 MB', () => {
  assert.equal(nameError(' A '), 'Type your name, at least 2 characters.')
  assert.equal(nameError('Ana Ceban'), '')
  assert.equal(nameError('x'.repeat(101)), 'Keep it under 100 characters.')
  assert.equal(phoneError(''), '')
  assert.equal(phoneError('+373 69 123 456'), '')
  assert.equal(phoneError('(+1) 212-555-0100'), '')
  assert.match(phoneError('069 123'), /country code/)
  assert.match(phoneError('call me'), /country code/)
  assert.equal(photoError({ type: 'image/png', size: 1_200_000 }), '')
  assert.equal(photoError({ type: 'image/gif', size: 10 }), 'That file isn’t a JPG, PNG or WEBP picture.')
  assert.equal(photoError({ type: 'image/jpeg', size: 3_400_000 }), 'That photo is 3.4 MB; the limit is 2 MB.')
})

test('the session says when it ends, in local time', () => {
  const now = new Date(2026, 10, 8, 12, 2).getTime()
  assert.equal(whenText(new Date(2026, 10, 8, 18, 30).getTime(), now), 'today at 18:30')
  assert.equal(whenText(new Date(2026, 10, 9, 12, 2).getTime(), now), 'tomorrow at 12:02')
  assert.equal(whenText(new Date(2026, 10, 12, 9, 5).getTime(), now), 'on 12 Nov at 09:05')
  assert.match(sessionText(new Date(2026, 10, 9, 12, 2).getTime(), now), /^This session ends tomorrow at 12:02\. A session lasts 24 hours/)
  assert.match(sessionText(now - 1, now), /^This session has ended\./)
  assert.match(sessionText(undefined, now), /^A session lasts 24 hours/)
})
