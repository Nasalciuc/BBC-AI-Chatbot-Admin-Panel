// The follow-up lottery in the offers queue (src/features/tasks/offers.ts): how a lead reached its agent, in one
// line, and the "Mine" view. Server half: bbc-chatbot-api app/services/offers_view.py (followup_line).

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { followupText, mineOnly } from '../src/features/tasks/offers.ts'
import { type LeadBeat, leadHref, leadToAnnounce, newLeadText, tasksBadge } from '../src/lib/new-leads.ts'
import { MELODY_NOTES } from '../src/lib/alert-prefs.ts'

const read = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8')

const at = '2026-11-21T09:12:00Z'
const hhmm = () => '09:12'

test('each state of the lottery says who and how', () => {
  assert.equal(followupText({ by: 'lottery', state: 'pending', at, agent_name: 'Ana Agent' }, hhmm),
    'Given by the lottery to Ana at 09:12: waiting for Ana to open it')
  assert.equal(followupText({ by: 'lottery', state: 'acked', at, agent_name: 'Ana Agent' }, hhmm), 'Ana took it from the lottery at 09:12')
  assert.equal(followupText({ by: 'manager', state: 'manual', at, agent_name: 'Bob Builder' }, hhmm), 'Given to Bob by a manager at 09:12')
  assert.match(followupText({ by: 'lottery', state: 'missed', at, agent_name: 'Ana Agent' }, hhmm), /didn’t open it in time/)
})

test('no line for a lead that did not come through the lottery', () => {
  assert.equal(followupText(null), null)
  assert.equal(followupText(undefined), null)
  assert.equal(followupText({ by: 'lottery', state: 'pending', at, agent_name: null }), null)
  assert.equal(followupText({ by: 'lottery', state: 'weird', at, agent_name: 'Ana' }), null)
})

test('mine is mine, and nobody is nothing', () => {
  const rows = [{ agent_id: 'me' }, { agent_id: 'other' }, { agent_id: null }, {}]
  assert.deepEqual(mineOnly(rows, 'me'), [{ agent_id: 'me' }])
  assert.deepEqual(mineOnly(rows, undefined), [])
})

test('the badge of leads given to me is server data: the query cache, not a store (CodeRabbit #273)', () => {
  const read = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8')
  assert.doesNotMatch(read('src/stores/live-store.ts'), /followupsNew/)
  // The beat writes it — null when the server says nothing, the number (0 included) when it does…
  assert.match(read('src/hooks/use-heartbeat.ts'),
    /queryClient\.setQueryData<number \| null>\(FOLLOWUPS_NEW_KEY, typeof res\?\.followups_new === 'number' \? res\.followups_new : null\)/)
  // …the menu reads it from the cache, which a sign-out clears with the rest.
  assert.match(read('src/components/layout/app-sidebar.tsx'), /const followupsNew = useFollowupsNew\(\)/)
  assert.match(read('src/hooks/use-followups-new.ts'), /queryFn: skipToken/)
  assert.match(read('src/components/sign-out-dialog.tsx'), /queryClient\.clear\(\)/)
})

// ── A lead given to me: the alert (the owner, 22 Nov 2026) ──────────────────────────────────────────────

const beat = (b: Partial<LeadBeat>): LeadBeat => ({ before: 1, count: 2, newest: 'c2', announced: new Set(['c1']), viewing: null, ...b })

test('a lead given since the last beat is announced, once', () => {
  assert.equal(leadToAnnounce(beat({})), 'c2')
  assert.equal(leadToAnnounce(beat({ announced: new Set(['c1', 'c2']) })), null, 'never twice')
  assert.equal(leadToAnnounce(beat({ before: 0, count: 1, newest: 'c9', announced: new Set() })), 'c9')
})

test('rule 5: the beats that announce nothing', () => {
  assert.equal(leadToAnnounce(beat({ before: null })), null, 'the first beat is the baseline: what was there at sign-in')
  assert.equal(leadToAnnounce(beat({ count: 1 })), null, 'the same count: nothing new')
  assert.equal(leadToAnnounce(beat({ before: 2, count: 1, newest: 'c1', announced: new Set() })), null,
    'opening the newest leaves an older one newest: not news')
  assert.equal(leadToAnnounce(beat({ count: null })), null, 'the server can not say')
  assert.equal(leadToAnnounce(beat({ newest: null })), null)
  assert.equal(leadToAnnounce(beat({ viewing: 'c2' })), null, 'the chat on screen')
})

test('the toast and the notification say what came, and Open goes to the chat', () => {
  assert.deepEqual(newLeadText(1), { title: 'New lead given to you', body: 'A client with a trip and a contact is waiting for your offer.' })
  assert.equal(newLeadText(3).title, '3 new leads given to you')
  assert.equal(leadHref('c0000000-0000-4000-8000-000000000001'), '/chats?highlight=c0000000-0000-4000-8000-000000000001')
})

test('the Tasks badge counts the late tasks and the new leads together, and says each', () => {
  assert.deepEqual(tasksBadge(2, 1), { text: '3', label: '2 late · 1 new lead given to you' })
  assert.deepEqual(tasksBadge(0, 3), { text: '3', label: '3 new leads given to you' })
  assert.deepEqual(tasksBadge(4, null), { text: '4', label: '4 late' }, 'no number from the server: the late tasks alone')
  assert.equal(tasksBadge(0, 0), null)
  assert.equal(tasksBadge(0, null), null)
})

test('a new lead plays my sound once (the gentle melody unless I chose another): no loop, no flashing tab', () => {
  const end = Math.max(...MELODY_NOTES.map(([, start, len]) => start + len))
  assert.ok(end >= 2.5 && end <= 4, String(end))
  for (const [freq] of MELODY_NOTES) assert.ok(freq >= 300 && freq <= 1000, String(freq))
  const src = read('src/lib/notify-assignment.ts')
  const fn = src.slice(src.indexOf('export function notifyNewLead'), src.indexOf('export function stopAssignmentAlerts'))
  assert.match(fn, /_playOnce\(prefs\.sound, prefs\.volume \/ 100\)/)
  assert.doesNotMatch(fn, /setInterval|_flashInterval|requireInteraction/, 'no loop, no flashing tab, nothing that stays')
})

test('the beat announces a new lead, and the menu shows the badge with its label on hover', () => {
  const hook = read('src/hooks/use-heartbeat.ts')
  assert.match(hook, /const lead = leadToAnnounce\(\{ before, count, newest, announced: leadsAnnounced\.current, viewing: viewingRef\.current \}\)/)
  assert.match(hook, /notifyNewLead\(text\.title, text\.body, leadHref\(lead\)\)/)
  assert.match(hook, /action: \{ label: 'Open', onClick: \(\) => window\.location\.assign\(leadHref\(lead\)\) \}/)
  const menu = read('src/components/layout/app-sidebar.tsx')
  assert.match(menu, /if \(item\.key === 'tasks'\) return tasksBadge\(taskCount, followupsNew\)/)
  assert.match(menu, /title=\{badge \? \(mini \? `\$\{item\.title\}, \$\{badge\.label\}` : badge\.label\)/)
})
