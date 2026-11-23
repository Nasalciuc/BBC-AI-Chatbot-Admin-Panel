// The chats list's search box and filter row (src/features/chats/search.ts). Server half: bbc-chatbot-api
// app/services/chat_search.py (parse) and the filters of GET /api/conversations.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { EMPTY_FILTERS, MESSAGES_RESTRICTED, MESSAGE_READERS, SEARCH_DEBOUNCE_MS, SEARCH_MAX, canSearchMessages, filterCount, filterKey, filterParams, messagesSearchable, searchElsewhere, searchEmptyText, searchHint } from '../src/features/chats/search.ts'

test('the box says what it will look for, as the server reads it', () => {
  assert.equal(searchHint('#3403'), 'The chat number')
  assert.equal(searchHint('3403'), 'The chat number')
  assert.equal(searchHint('dana@'), 'An e-mail')
  assert.equal(searchHint('+1 (214) 208-5185'), 'A phone, by its digits')
  assert.equal(searchHint('IST'), 'An airport, departure or arrival')
  assert.equal(searchHint('Istanbul'), 'A name, an e-mail, a phone, or a city in the route')
  assert.equal(searchHint('   '), '')
})

test('for a supervisor, whose rows are masked, the box says the name and the contact match only whole', () => {
  assert.equal(searchHint('dana@', true), 'An e-mail, the whole address')
  assert.equal(searchHint('+1 (214) 208-5185', true), 'A phone, the whole number')
  assert.equal(searchHint('Grace', true), 'Whole words of a name, or a city in the route')
  // What the mask doesn't touch reads the same for everyone.
  assert.equal(searchHint('#3403', true), 'The chat number')
  assert.equal(searchHint('IST', true), 'An airport, departure or arrival')
  assert.equal(searchHint('   ', true), '')
})

test('the chats page tells the box when its rows are masked: a supervisor only', () => {
  const src = readFileSync(new URL('../src/features/chats/index.tsx', import.meta.url), 'utf8')
  assert.match(src, /searchHint\(search, role === 'supervisor'\)/)
})

test('who may search what was said is the server’s list, word for word', () => {
  const api = readFileSync(new URL('../../bbc-chatbot-api/app/api/search.py', import.meta.url), 'utf8')
  const m = api.match(/MESSAGE_READERS = \{([^}]*)\}/)
  assert.ok(m, 'MESSAGE_READERS in app/api/search.py')
  const server = [...m[1].matchAll(/"([a-z_]+)"/g)].map((x) => x[1]).sort()
  assert.deepEqual([...MESSAGE_READERS].sort(), server)
  for (const role of ['owner', 'admin', 'dev', 'qa', 'sales', 'support']) assert.equal(canSearchMessages(role), true, role)
  for (const role of ['supervisor', 'project_manager', '']) assert.equal(canSearchMessages(role), false, role)
})

test('a role the server refuses sees "In what was said" locked with the reason, and asks nothing', () => {
  const src = readFileSync(new URL('../src/features/chats/index.tsx', import.meta.url), 'utf8')
  assert.match(src, /const wantMessages = messagesAllowed && inMessages && messagesSearchable\(debouncedSearch\)/)
  const locked = src.slice(src.indexOf('title={MESSAGES_RESTRICTED}') - 200, src.indexOf('title={MESSAGES_RESTRICTED}') + 400)
  assert.match(locked, /disabled title=\{MESSAGES_RESTRICTED\}/)
  assert.match(locked, /<Lock aria-hidden='true'/)
  assert.ok(MESSAGES_RESTRICTED.length > 0)
})

test('no filter is no parameter and no key part', () => {
  assert.deepEqual(filterParams(EMPTY_FILTERS), {})
  assert.equal(filterKey(EMPTY_FILTERS), '')
  assert.equal(filterCount(EMPTY_FILTERS), 0)
})

test('every filter reaches the query in the server’s words', () => {
  const f = { dateFrom: '2026-11-20', dateTo: '2026-11-21', contact: 'no', source: 'kayak', routeFrom: 'jfk', routeTo: 'IST', crm: 'out' } as const
  assert.deepEqual(filterParams(f), {
    date_from: '2026-11-20', date_to: '2026-11-21', has_contact: 'false', source: 'kayak', route_from: 'JFK', route_to: 'IST', crm: 'out',
  })
  assert.equal(filterCount(f), 6, 'the two days count as one filter')
  assert.equal(filterKey(f), 'crm=out&date_from=2026-11-20&date_to=2026-11-21&has_contact=false&route_from=JFK&route_to=IST&source=kayak')
})

test('half-typed values are not sent', () => {
  assert.deepEqual(filterParams({ ...EMPTY_FILTERS, routeFrom: 'JF', dateFrom: '2026-11' }), {})
})

test('what was said is searched from three characters', () => {
  assert.equal(messagesSearchable('ab'), false)
  assert.equal(messagesSearchable(' abc '), true)
})

test('the box holds no more than the server takes, 100 characters (CodeRabbit #273)', () => {
  assert.equal(SEARCH_MAX, 100)
  assert.equal(messagesSearchable('x'.repeat(100)), true)
  assert.equal(messagesSearchable('x'.repeat(101)), false)
  assert.match(readFileSync(new URL('../src/features/chats/index.tsx', import.meta.url), 'utf8'), /id='chat-search'[\s\S]{0,200}maxLength=\{SEARCH_MAX\}/)
})

const read = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8')

test('the requests wait for a pause in the typing, not one per keystroke (CodeRabbit #273)', () => {
  const page = read('src/features/chats/index.tsx')
  assert.doesNotMatch(page, /useDeferredValue/, 'a deferred value still hands every prefix on')
  assert.match(page, /const debouncedSearch = useDebouncedValue\(search, SEARCH_DEBOUNCE_MS\)/)
  assert.match(page, /queryKey: \['message-hits', debouncedSearch\.trim\(\)\], queryFn: \(\) => searchMessages\(debouncedSearch\.trim\(\)\)/)
  assert.ok(SEARCH_DEBOUNCE_MS >= 300 && SEARCH_DEBOUNCE_MS <= 600, String(SEARCH_DEBOUNCE_MS))
  const hook = read('src/hooks/use-debounced-value.ts')
  assert.match(hook, /setTimeout\(\(\) => setSettled\(value\), ms\)/)
  assert.match(hook, /return \(\) => clearTimeout\(timer\)/)
})

test('the chats opened lately stay while focus is in the box or the list (CodeRabbit #273)', () => {
  const page = read('src/features/chats/index.tsx')
  const box = page.slice(page.indexOf("className='relative flex flex-1'"), page.indexOf("<label htmlFor='chat-search'"))
  assert.match(box, /if \(region\.contains\(e\.relatedTarget as Node \| null\)\) return/)
  // Focus back in the region by the time the close is looked at keeps the list (a quick blur and refocus).
  assert.match(box, /setTimeout\(\(\) => \{ if \(!region\.contains\(document\.activeElement\)\) setSearchFocused\(false\) \}, 150\)/)
  // The input no longer closes the list on its own blur, whatever has the focus next.
  const input = page.slice(page.indexOf("id='chat-search'"), page.indexOf("placeholder:text-k-8a8a8a"))
  assert.doesNotMatch(input, /onBlur|onFocus/)
})

test('what was said scrolls in its own region (CodeRabbit #273)', () => {
  const page = read('src/features/chats/index.tsx')
  assert.match(page, /<section aria-label='What was said' className=\{cn\('flex max-h-\[35vh\] flex-none flex-col gap-1 overflow-y-auto/)
})

test('a search that finds nothing on its tab offers the tab one wider, the owner’s Mine first (secret-site run, 22 Nov)', () => {
  const manager = ['my_active', 'all_active', 'to_read', 'all_closed']
  assert.equal(searchElsewhere('my_active', manager), 'all_active')
  assert.equal(searchElsewhere('to_read', manager), 'all_active')
  assert.equal(searchElsewhere('all_active', manager), 'all_closed')
  assert.equal(searchElsewhere('all_closed', manager), null)
  // An agent: Mine, then their own Closed; nothing wider.
  assert.equal(searchElsewhere('my_active', ['my_active', 'my_closed']), 'my_closed')
  assert.equal(searchElsewhere('my_closed', ['my_active', 'my_closed']), null)
  // Admin, supervisor, QA have no Mine: All active, then Closed.
  assert.equal(searchElsewhere('all_active', ['all_active', 'to_read', 'all_closed']), 'all_closed')
  const src = readFileSync(new URL('../src/features/chats/index.tsx', import.meta.url), 'utf8')
  assert.match(src, /const searchMoreKey = debouncedSearch && !idleOnly \? searchElsewhere\(activeTab, visibleTabs\.map\(\(t\) => t\.key\)\) : null/)
  assert.match(src, /onClick=\{\(\) => \{ setActiveTab\(searchMoreTab\.key\); setSelectedId\(null\) \}\}>\s+Look in \{searchMoreTab\.label\}/)
})

test('with what was said listed above, the list’s empty line says what it looked in (secret-site run, 22 Nov)', () => {
  assert.equal(searchEmptyText('Seoul in May', 'All active', true), 'No chat’s name, contact or route matches “Seoul in May”. Where it was said is above.')
  assert.equal(searchEmptyText('Seoul in May', 'Mine', false), 'No chat in Mine matches “Seoul in May”.')
  const src = readFileSync(new URL('../src/features/chats/index.tsx', import.meta.url), 'utf8')
  assert.match(src, /searchEmptyText\(debouncedSearch, tab\.label, wantMessages && \(hits\.data \?\? \[\]\)\.length > 0\)/)
  assert.doesNotMatch(src, /`No chat matches “\$\{debouncedSearch\}”\.`/)
})
