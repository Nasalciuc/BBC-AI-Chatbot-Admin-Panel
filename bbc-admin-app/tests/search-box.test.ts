// The chats list's search box and filter row (src/features/chats/search.ts). Server half: bbc-chatbot-api
// app/services/chat_search.py (parse) and the filters of GET /api/conversations.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { EMPTY_FILTERS, SEARCH_DEBOUNCE_MS, filterCount, filterKey, filterParams, messagesSearchable, searchHint } from '../src/features/chats/search.ts'

test('the box says what it will look for, as the server reads it', () => {
  assert.equal(searchHint('#3403'), 'The chat number')
  assert.equal(searchHint('3403'), 'The chat number')
  assert.equal(searchHint('dana@'), 'An e-mail')
  assert.equal(searchHint('+1 (214) 208-5185'), 'A phone, by its digits')
  assert.equal(searchHint('IST'), 'An airport, departure or arrival')
  assert.equal(searchHint('Istanbul'), 'A name, an e-mail, a phone, or a city in the route')
  assert.equal(searchHint('   '), '')
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
