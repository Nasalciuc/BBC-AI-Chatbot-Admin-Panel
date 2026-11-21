// The chats list's search box and filter row (src/features/chats/search.ts). Server half: bbc-chatbot-api
// app/services/chat_search.py (parse) and the filters of GET /api/conversations.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { EMPTY_FILTERS, filterCount, filterKey, filterParams, messagesSearchable, searchHint } from '../src/features/chats/search.ts'

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
