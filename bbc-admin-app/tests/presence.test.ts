// Honest presence display — full matrix over (state × freshness).
// Runs with: node --experimental-strip-types --test tests/presence.test.ts

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { describeClientPresence, presenceFromMetadata } from '../src/features/chats/presence.ts'

const NOW = new Date('2026-08-07T12:00:00Z')
const ago = (mins: number) => new Date(NOW.getTime() - mins * 60000).toISOString()

test('online + fresh → green "Active now"', () => {
  const d = describeClientPresence('online', ago(1), NOW)
  assert.match(d.label, /^Active now/)   // now carries the age: "Active now · last seen 12s ago"
  assert.equal(d.tone, 'green')
})

test('online + 10 min old → aged, NOT green', () => {
  const d = describeClientPresence('online', ago(10), NOW)
  assert.equal(d.label, 'Active · 10m ago')
  assert.equal(d.tone, 'neutral')
})

test('minimized fresh → on site, no age suffix', () => {
  const d = describeClientPresence('minimized', ago(1), NOW)
  assert.equal(d.label, 'On site, chat closed')
  assert.equal(d.tone, 'neutral')
})

test('minimized 30 min → aged suffix', () => {
  const d = describeClientPresence('minimized', ago(30), NOW)
  assert.equal(d.label, 'On site, chat closed · 30m ago')
})

test('left 30 seconds ago → grace, NOT red', () => {
  const d = describeClientPresence('left', new Date(NOW.getTime() - 30000).toISOString(), NOW)
  assert.equal(d.label, 'Left just now')
  assert.equal(d.tone, 'neutral')
})

test('left 10 min ago → red, EARNED', () => {
  const d = describeClientPresence('left', ago(10), NOW)
  assert.equal(d.label, 'Left · 10m ago')
  assert.equal(d.tone, 'red')
})

test('hours formatting: 80m → 1h 20m ago; 120m → 2h ago', () => {
  assert.equal(describeClientPresence('left', ago(80), NOW).label, 'Left · 1h 20m ago')
  assert.equal(describeClientPresence('left', ago(120), NOW).label, 'Left · 2h ago')
})

test('unknown / missing → neutral "Unknown", never red', () => {
  for (const presence of [undefined, '', 'weird'] as const) {
    const d = describeClientPresence(presence as string | undefined, undefined, NOW)
    assert.equal(d.label, 'Unknown')
    assert.equal(d.tone, 'neutral')
  }
})

test('left without timestamp → red without age (old data is stale by definition)', () => {
  const d = describeClientPresence('left', undefined, NOW)
  assert.equal(d.label, 'Left')
  assert.equal(d.tone, 'red')
})

test('presenceFromMetadata: prefers widget_presence, falls back to legacy shapes', () => {
  assert.equal(presenceFromMetadata({ widget_presence: 'online' }), 'online')
  assert.equal(presenceFromMetadata({ widget_open: true }), 'online')
  assert.equal(presenceFromMetadata({ widget_open: 'true' }), 'online')
  assert.equal(presenceFromMetadata({ widget_last_close_reason: 'left' }), 'left')
  assert.equal(presenceFromMetadata({}), undefined)
})
