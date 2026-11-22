// The follow-up lottery in the offers queue (src/features/tasks/offers.ts): how a lead reached its agent, in one
// line, and the "Mine" view. Server half: bbc-chatbot-api app/services/offers_view.py (followup_line).

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { followupText, mineOnly } from '../src/features/tasks/offers.ts'
import { type LeadBeat, leadHref, leadToAnnounce, newLeadText, nextSeen, tasksBadge } from '../src/lib/new-leads.ts'
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

// The beats as the heartbeat hook reads them, one after another: what each one announces.
type Beat = { count: number | null; newest?: string; at?: number; viewing?: string }
function announced(beats: Beat[]): string[] {
  let seen: number | null = null
  const out: string[] = []
  for (const b of beats) {
    const newest = b.newest ?? null
    const newestAt = b.at ?? null
    const lead = leadToAnnounce({ seen, newest, newestAt, viewing: b.viewing ?? null } satisfies LeadBeat)
    seen = nextSeen(seen, b.count, newestAt)
    if (lead) out.push(lead)
  }
  return out
}

test('a lead given since the last one seen is announced, once', () => {
  assert.deepEqual(announced([{ count: 0 }, { count: 1, newest: 'a', at: 10 }, { count: 1, newest: 'a', at: 10 }]), ['a'])
  assert.deepEqual(announced([{ count: 2, newest: 'a', at: 10 }, { count: 3, newest: 'b', at: 20 }]), ['b'],
    'the first beat is the baseline: what was there at sign-in is not news')
  assert.deepEqual(announced([{ count: 0 }, { count: 2, newest: 'b', at: 20 }]), ['b'], 'two at once: one alert, for the last')
})

test('opening A while B arrives: the count stays, and B is news (CodeRabbit #273)', () => {
  assert.deepEqual(announced([{ count: 1, newest: 'a', at: 10 }, { count: 1, newest: 'b', at: 20 }]), ['b'])
})

test('a newest that could not be read is announced when the read comes back (CodeRabbit #273)', () => {
  assert.deepEqual(announced([{ count: 0 }, { count: 1 }, { count: 1, newest: 'a', at: 10 }]), ['a'])
  assert.deepEqual(announced([{ count: null }, { count: 0 }, { count: 1, newest: 'a', at: 5 }]), ['a'],
    'no number at sign-in: the baseline waits for one')
})

test('rule 5: the beats that announce nothing', () => {
  assert.deepEqual(announced([{ count: 2, newest: 'y', at: 20 }, { count: 1, newest: 'x', at: 10 }]), [],
    'opening the newest leaves an older one newest: not news')
  assert.deepEqual(announced([{ count: 0 }, { count: 1, newest: 'b', at: 10, viewing: 'b' }, { count: 1, newest: 'b', at: 10 }]), [],
    'the chat on screen, and not later either')
  assert.deepEqual(announced([{ count: null }, { count: null }]), [])
  assert.equal(nextSeen(30, 0, null), 30, 'nothing left to open keeps what was seen')
  assert.equal(nextSeen(null, 0, null), 0, 'none at sign-in: the next lead is news')
  assert.equal(nextSeen(null, 2, null), null, 'leads without their newest: no baseline yet')
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
  assert.match(hook, /const lead = leadToAnnounce\(\{ seen: leadSeen\.current, newest, newestAt, viewing: viewingRef\.current \}\)/)
  assert.match(hook, /leadSeen\.current = nextSeen\(leadSeen\.current, count, newestAt\)/)
  assert.match(hook, /const newestAt = typeof res\?\.followup_newest_at === 'number' \? res\.followup_newest_at : null/)
  assert.match(hook, /notifyNewLead\(text\.title, text\.body, leadHref\(lead\)\)/)
  assert.match(hook, /action: \{ label: 'Open', onClick: \(\) => window\.location\.assign\(leadHref\(lead\)\) \}/)
  const menu = read('src/components/layout/app-sidebar.tsx')
  assert.match(menu, /if \(item\.key === 'tasks'\) return tasksBadge\(taskCount, followupsNew\)/)
  assert.match(menu, /title=\{badge \? \(mini \? `\$\{item\.title\}, \$\{badge\.label\}` : badge\.label\)/)
})
