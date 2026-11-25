// The chat the proactive card opened (no form), as the panel shows it: why it opened, what the client
// searched on the page (not confirmed by them), the replay of the visit, and the departure task.
// Server halves: bbc-chatbot-api app/services/proactive.py and app/services/departure.py.
// Runs with: node --experimental-strip-types --test tests/proactive-panel.test.ts

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  CARD_NO_CONTACT, CARD_ONLY, SOURCES_RESTRICTED, canSeeSources, cardNoContact, openedByText, pageTripText, replayUrl,
  siteName, whereRows,
} from '../src/features/chats/view.ts'
import { unownedNote } from '../src/features/tasks/view.ts'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const panel = readFileSync(join(root, 'src/features/chats/components/details-panel.tsx'), 'utf-8')
const taskRow = readFileSync(join(root, 'src/features/tasks/components/task-row.tsx'), 'utf-8')
const types = readFileSync(join(root, 'src/lib/types.ts'), 'utf-8')

const NOW = new Date('2026-11-16T10:00:00Z')

test('why the chat opened by itself', () => {
  assert.equal(openedByText({ proactive_trigger: 'kayak' }, NOW), 'By itself: the client came from Kayak')
  assert.equal(openedByText({ proactive_trigger: 'ads' }, NOW), 'By itself: the client came from a Google or Bing ad')
  assert.equal(openedByText({ proactive_trigger: 'social' }, NOW), 'By itself: the client came from a Facebook or Instagram ad')
  assert.equal(openedByText({ proactive_trigger: 'quote' }, NOW), 'By itself: the client pressed “Get free quote” and stopped')
  assert.equal(openedByText({ proactive_trigger: 'reader' }, NOW), 'By itself: the client read the page for a while')
  assert.equal(openedByText({ proactive_trigger: 'exit' }, NOW), 'By itself: the client was about to leave')
  assert.equal(openedByText({ proactive_trigger: 'something_new' }, NOW), 'By itself: the client hesitated')
  // The same instant as NOW: the same local day in every zone (a fixed 09:41Z is the day before in Honolulu).
  assert.match(openedByText({ proactive_trigger: 'kayak', proactive_at: NOW.toISOString() }, NOW), /^By itself: the client came from Kayak · \d{2}:\d{2}$/)
  assert.equal(openedByText({}, NOW), '')
  // Only the table's own words: a key that reaches Object.prototype is an unknown one.
  assert.equal(openedByText({ proactive_trigger: 'constructor' }, NOW), 'By itself: the client hesitated')
  assert.equal(openedByText(undefined, NOW), '')
})

test('the note the client tapped is no chat that opened by itself, and says why no form came first', () => {
  assert.equal(openedByText({ proactive_trigger: 'note' }, NOW), 'No form: the client tapped the note beside the button')
  assert.match(openedByText({ proactive_trigger: 'note', proactive_at: NOW.toISOString() }, NOW), /^No form: the client tapped the note beside the button · \d{2}:\d{2}$/)
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
  assert.equal(pageTripText({ proactive_trip: { destination_name: 'Paris', cabin: 'toString' } }), 'To Paris')
  // A day the month doesn't have is no day (not "3 Mar"), and a count is a whole number above zero.
  assert.equal(
    pageTripText({ proactive_trip: { origin_code: 'ATL', destination_code: 'ICN', depart_date: '2027-02-31', return_date: '2027-13-01', adults: 2.5 } }),
    'ATL → ICN',
  )
  assert.equal(pageTripText({ proactive_trip: { destination_code: 'ICN', adults: 2, children: -1, infants: 0.5 } }), 'To ICN · 2 adults')
  assert.equal(pageTripText({ proactive_trip: { destination_code: 'ICN', depart_date: '2028-02-29' } }), 'To ICN · 29 Feb')
  assert.equal(pageTripText({}), '')
})

test('the replay link: only for an id, under an https base from the build', () => {
  const id = '0192f3a4-5b6c-7d8e-9f01-23456789abcd'
  const base = 'https://eu.posthog.example/project/1/replay/'
  assert.equal(replayUrl({ posthog_session_id: id }, base), `${base}${id}`)
  assert.equal(replayUrl({ posthog_session_id: id }, 'https://eu.posthog.example/project/1/replay'), `${base}${id}`)
  assert.equal(replayUrl({ posthog_session_id: 'javascript:alert(1)' }, base), '')
  assert.equal(replayUrl({ posthog_session_id: 'short' }, base), '')
  assert.equal(replayUrl({}, base), '')
  // No base, or one that isn't a plain https address: no link (the Replay row is left out).
  assert.equal(replayUrl({ posthog_session_id: id }, undefined), '')
  assert.equal(replayUrl({ posthog_session_id: id }, ''), '')
  assert.equal(replayUrl({ posthog_session_id: id }, 'not a url'), '')
  assert.equal(replayUrl({ posthog_session_id: id }, 'http://eu.posthog.example/project/1/replay/'), '')
  assert.equal(replayUrl({ posthog_session_id: id }, 'javascript:alert(1)//'), '')
  assert.equal(replayUrl({ posthog_session_id: id }, 'https://eu.posthog.example/replay/?x=1'), '')
  assert.equal(replayUrl({ posthog_session_id: id }, 'https://user:pw@eu.posthog.example/replay/'), '')
})

test('the details panel: opened, from the page, replay — the replay never for agents or supervisors', () => {
  assert.match(panel, /const replay = p\.canSeeSources \? replayUrl\(conv\.metadata, REPLAY_BASE\) : ''/)
  // The address comes from the build, not from the code (bbc-admin-app/CLAUDE.md: never hardcode URLs).
  assert.match(panel, /const REPLAY_BASE = import\.meta\.env\.VITE_POSTHOG_REPLAY_URL as string \| undefined/)
  assert.doesNotMatch(readFileSync(join(root, 'src/features/chats/view.ts'), 'utf-8'), /posthog\.com/)
  // A closed chat with no lead still shows the page's search.
  assert.match(panel, /\{\(lead \|\| pageTrip \|\| \(conv\.tunnel === 'sales' && owner !== 'closed'\)\) && \(/)
  assert.match(panel, /\.\.\.\(opened \? \[\{ label: 'Opened', value: opened, strong: true \}\] : \[\]\)/)
  assert.match(panel, /<strong className='text-k-202020'>From the page:<\/strong> \{pageTrip\}/)
  // Always true, whatever the chat did since: the Trip rows above are what the client said. A note that
  // the trip is unconfirmed would stay after the client confirmed or changed it.
  assert.match(panel, /What the client searched before\s+writing, not what they said in the chat\./)
  assert.doesNotMatch(panel, /confirmed it in the chat/)
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

test('a card chat with no contact says so on its row, once (owner, 22 Nov)', () => {
  assert.equal(CARD_NO_CONTACT, 'Card · no contact')
  assert.equal(cardNoContact({ metadata: { proactive_trigger: 'kayak' }, has_contact: false }), CARD_NO_CONTACT)
  assert.equal(cardNoContact({ metadata: { proactive_trigger: 'note' }, has_contact: false }), CARD_NO_CONTACT)
  // A contact left, an older server that can't say, a chat the card didn't open, a blind row: nothing.
  assert.equal(cardNoContact({ metadata: { proactive_trigger: 'kayak' }, has_contact: true }), null)
  assert.equal(cardNoContact({ metadata: { proactive_trigger: 'kayak' } }), null)
  assert.equal(cardNoContact({ metadata: {}, has_contact: false }), null)
  assert.equal(cardNoContact({ metadata: { proactive_trigger: '  ' }, has_contact: false }), null)
  assert.equal(cardNoContact({ metadata: { widget_presence: 'left' }, has_contact: false }), null)
  assert.equal(cardNoContact({ has_contact: false }), null)
  // "No contact" said once: beside the "Left … · no contact" line the row names the card alone (CodeRabbit #273).
  assert.equal(cardNoContact({ metadata: { proactive_trigger: 'kayak' }, has_contact: false }, true), CARD_ONLY)
  assert.equal(cardNoContact({ metadata: { proactive_trigger: 'kayak' }, has_contact: true }, true), null)
  const list = readFileSync(join(root, 'src/features/chats/index.tsx'), 'utf-8')
  assert.match(list, /const cardOnly = cardNoContact\(conv, Boolean\(gone\)\)/)
  // A screen reader hears it too: the row's name says it (CodeRabbit #273).
  const named = list.split('\n').find((l) => l.includes('aria-label={`Conversation ')) ?? ''
  assert.ok(named.includes("${cardOnly ? `, ${cardOnly}` : ''}"), named)
  assert.match(list, /title=\{CARD_NO_CONTACT_TITLE\}/)
})

// ── Where the client came from is not an agent's (the owner, 25 Nov 2026) ──────────────────────────────────

test('who opens "Where they came from": not agents, not supervisors', () => {
  for (const role of ['sales', 'support', 'supervisor', undefined, '']) assert.equal(canSeeSources(role), false, String(role))
  // Rule 5: every other role keeps it as it was.
  for (const role of ['owner', 'admin', 'dev', 'qa', 'project_manager']) assert.equal(canSeeSources(role), true, role)
  assert.equal(SOURCES_RESTRICTED, 'Not for your role: where the client came from is marketing data')
})

test('an agent reads why the card chat has no name, without the source', () => {
  const at = { proactive_at: NOW.toISOString() }
  for (const trigger of ['kayak', 'ads', 'social']) {
    assert.equal(openedByText({ proactive_trigger: trigger }, NOW, { sources: false }), 'By itself: Skye’s card, no form')
    assert.match(openedByText({ proactive_trigger: trigger, ...at }, NOW, { sources: false }), /^By itself: Skye’s card, no form · \d{2}:\d{2}$/)
  }
  // What the client did is no source: the same words for everyone.
  assert.equal(openedByText({ proactive_trigger: 'quote' }, NOW, { sources: false }), 'By itself: the client pressed “Get free quote” and stopped')
  assert.equal(openedByText({ proactive_trigger: 'exit' }, NOW, { sources: false }), 'By itself: the client was about to leave')
  assert.equal(openedByText({ proactive_trigger: 'note' }, NOW, { sources: false }), 'No form: the client tapped the note beside the button')
  assert.equal(openedByText({}, NOW, { sources: false }), '')
  // Rule 5: a reader who sees sources reads them, as before.
  assert.equal(openedByText({ proactive_trigger: 'kayak' }, NOW, { sources: true }), 'By itself: the client came from Kayak')
})

test('the site is the brand: kept for the agent, in "This chat"', () => {
  assert.equal(siteName({ site: 'bbc' }), 'buybusinessclass.com')
  assert.equal(siteName({ site: ' bct ' }), 'businessclass-tickets.com')
  assert.equal(siteName({ site: 'other.example' }), 'other.example')
  assert.equal(siteName({ site: 3 }), '')
  assert.equal(siteName(undefined), '')
  assert.deepEqual(whereRows({ site: 'bct' }).map((r) => [r.label, r.value]), [['Site', 'businessclass-tickets.com']])
  assert.match(panel, /const site = p\.canSeeSources \? '' : siteName\(conv\.metadata\)/)
  assert.match(panel, /\.\.\.\(site \? \[\{ label: 'Site', value: site \}\] : \[\]\),/)
  assert.match(panel, /const opened = openedByText\(conv\.metadata, now, \{ sources: p\.canSeeSources \}\)/)
})

test('the fold and the list filter are locked for them: shown, disabled, a lock and the reason', () => {
  // The fold: the button is disabled, with the reason as its title and in its name; nothing opens.
  // Shown to them whatever their rows hold: the rows come without the sources, so the list of them is empty
  // (CodeRabbit #282, round 3).
  assert.match(panel, /\{p\.canSeeSources \? \(where\.length > 0 && \(/)
  const locked = panel.slice(panel.indexOf('{p.canSeeSources ? (where.length > 0 && ('))
  assert.ok(locked.length > 0)
  const lockedFold = locked.slice(locked.indexOf(') : ('), locked.indexOf('</section>'))
  assert.match(lockedFold, /disabled\s+title=\{SOURCES_RESTRICTED\}/)
  assert.match(lockedFold, /aria-label=\{`Where \$\{named \? first : 'they'\} came from \(\$\{SOURCES_RESTRICTED\.toLowerCase\(\)\}\)`\}/)
  assert.match(lockedFold, /<Lock aria-hidden='true'/)
  assert.doesNotMatch(lockedFold, /<Rows|onClick|onToggle/)
  const detail = readFileSync(join(root, 'src/features/chats/detail.tsx'), 'utf-8')
  assert.match(detail, /canSeeSources=\{canSeeSources\(role\)\}/)
  // The list's "Came from": chips that can't be pressed, "Anywhere" the one on; the working chips only for the rest.
  const list = readFileSync(join(root, 'src/features/chats/index.tsx'), 'utf-8')
  const row = list.slice(list.indexOf('{canSeeSources(role) ? ('), list.indexOf("<span className='text-xs text-k-555555'>Trip (airport codes)</span>"))
  assert.match(row, /onClick=\{\(\) => setFilter\('source', k\)\}/)
  const lockedRow = row.slice(row.indexOf(') : ('))
  assert.match(lockedRow, /title=\{SOURCES_RESTRICTED\}/)
  assert.match(lockedRow, /type='button' disabled aria-label=\{`\$\{label\} \(\$\{SOURCES_RESTRICTED\.toLowerCase\(\)\}\)`\}/)
  assert.match(lockedRow, /chipCls\(label === 'Anywhere'\)/)
  assert.doesNotMatch(lockedRow, /setFilter/)
})
