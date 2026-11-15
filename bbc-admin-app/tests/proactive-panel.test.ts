// The chat the proactive card opened (no form), as the panel shows it: why it opened, what the client
// searched on the page (not confirmed by them), the replay of the visit, and the departure task.
// Server halves: bbc-chatbot-api app/services/proactive.py and app/services/departure.py.
// Runs with: node --experimental-strip-types --test tests/proactive-panel.test.ts

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { openedByText, pageTripText, replayUrl } from '../src/features/chats/view.ts'
import { unownedNote } from '../src/features/tasks/view.ts'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const panel = readFileSync(join(root, 'src/features/chats/components/details-panel.tsx'), 'utf-8')
const taskRow = readFileSync(join(root, 'src/features/tasks/components/task-row.tsx'), 'utf-8')
const types = readFileSync(join(root, 'src/lib/types.ts'), 'utf-8')

const NOW = new Date('2026-11-16T10:00:00Z')

test('why the chat opened by itself', () => {
  assert.equal(openedByText({ proactive_trigger: 'kayak' }, NOW), 'By itself: the client came from Kayak')
  assert.equal(openedByText({ proactive_trigger: 'quote' }, NOW), 'By itself: the client pressed “Get free quote” and stopped')
  assert.equal(openedByText({ proactive_trigger: 'reader' }, NOW), 'By itself: the client read the page for a while')
  assert.equal(openedByText({ proactive_trigger: 'exit' }, NOW), 'By itself: the client was about to leave')
  assert.equal(openedByText({ proactive_trigger: 'something_new' }, NOW), 'By itself: the client hesitated')
  assert.match(openedByText({ proactive_trigger: 'kayak', proactive_at: '2026-11-16T09:41:00Z' }, NOW), /^By itself: the client came from Kayak · \d{2}:\d{2}$/)
  assert.equal(openedByText({}, NOW), '')
  assert.equal(openedByText(undefined, NOW), '')
})

test('what the client searched on the page', () => {
  const trip = {
    source: 'kayak', origin_code: 'ATL', destination_code: 'ICN', depart_date: '2027-05-25', return_date: '2027-06-28',
    cabin: 'first', adults: 4, children: 1, infants: 0,
  }
  assert.equal(pageTripText({ proactive_trip: trip }), 'ATL → ICN · 25 May – 28 Jun · 4 adults, 1 child · First')
  assert.equal(pageTripText({ proactive_trip: { source: 'route_page', destination_name: 'Paris', cabin: 'business' } }), 'To Paris · Business')
  assert.equal(pageTripText({ proactive_trip: { origin_name: 'Chicago' } }), 'From Chicago')
  assert.equal(pageTripText({ proactive_trip: { depart_date: 'soon', adults: 'four', cabin: 'luxury' } }), '')
  assert.equal(pageTripText({ proactive_trip: 'ATL-ICN' }), '')
  assert.equal(pageTripText({}), '')
})

test('the replay link: only for an id', () => {
  const id = '0192f3a4-5b6c-7d8e-9f01-23456789abcd'
  assert.equal(replayUrl({ posthog_session_id: id }), `https://eu.posthog.com/project/348558/replay/${id}`)
  assert.equal(replayUrl({ posthog_session_id: 'javascript:alert(1)' }), '')
  assert.equal(replayUrl({ posthog_session_id: 'short' }), '')
  assert.equal(replayUrl({}), '')
})

test('the details panel: opened, from the page, replay — the replay never for supervisors', () => {
  assert.match(panel, /const replay = p\.isSupervisor \? '' : replayUrl\(conv\.metadata\)/)
  assert.match(panel, /\.\.\.\(opened \? \[\{ label: 'Opened', value: opened, strong: true \}\] : \[\]\)/)
  assert.match(panel, /<strong className='text-k-202020'>From the page:<\/strong> \{pageTrip\}/)
  // A link out opens a new tab and gives the page nothing back.
  assert.match(panel, /<a href=\{r\.href\} target='_blank' rel='noopener noreferrer'/)
})

test('the departure task: its source and its Unowned reason', () => {
  assert.match(types, /export type TaskSource = 'caught' \| 'client_asked' \| 'added' \| 'dashboard' \| 'departure'/)
  assert.match(types, /unowned_reason: 'ai_only' \| 'left_shift' \| 'no_ready_agent' \| null/)
  assert.match(taskRow, /departure: 'bg-k-fdecea text-k-b3261e',/)
  assert.equal(
    unownedNote({ unowned_reason: 'no_ready_agent', unowned_from_name: null, unowned_at: null }, 'UTC'),
    'Nobody was Ready when the client left',
  )
})
