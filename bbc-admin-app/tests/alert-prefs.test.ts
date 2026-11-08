// Settings › Alerts — pinned on lib/alert-prefs.ts and the alert engine.
// Runs with: node --experimental-strip-types --test tests/alert-prefs.test.ts

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  DEFAULT_ALERT_PREFS,
  PERMISSION_TEXT,
  notificationText,
  notifyName,
  parseAlertPrefs,
  volumeLow,
} from '../src/lib/alert-prefs.ts'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (p: string) => readFileSync(join(root, p), 'utf-8')

test('untouched settings ring exactly as before: recording, full volume, flashing tab, queue chime', () => {
  assert.deepEqual(DEFAULT_ALERT_PREFS, { ring: true, flash: true, sound: 'classic', volume: 100, beep: true })
  assert.deepEqual(parseAlertPrefs(null), DEFAULT_ALERT_PREFS)
  assert.deepEqual(parseAlertPrefs(''), DEFAULT_ALERT_PREFS)
})

test('whatever is stored, a usable set comes back', () => {
  assert.deepEqual(parseAlertPrefs('not json'), DEFAULT_ALERT_PREFS)
  assert.deepEqual(parseAlertPrefs('[1,2]'), DEFAULT_ALERT_PREFS)
  assert.deepEqual(parseAlertPrefs('{"ring":false,"sound":"two","volume":45}'), { ...DEFAULT_ALERT_PREFS, ring: false, sound: 'two', volume: 45 })
  assert.equal(parseAlertPrefs('{"sound":"siren"}').sound, 'classic')
  assert.equal(parseAlertPrefs('{"volume":250}').volume, 100)
  assert.equal(parseAlertPrefs('{"volume":-4}').volume, 0)
  assert.equal(parseAlertPrefs('{"volume":"loud"}').volume, 100)
  assert.equal(parseAlertPrefs('{"flash":"yes"}').flash, true)
})

test('a low ring volume is called out; a muted ring is not "low"', () => {
  assert.equal(volumeLow({ ...DEFAULT_ALERT_PREFS, volume: 30 }), true)
  assert.equal(volumeLow({ ...DEFAULT_ALERT_PREFS, volume: 35 }), false)
  assert.equal(volumeLow({ ...DEFAULT_ALERT_PREFS, ring: false, volume: 10 }), false)
})

test('the notification shows a first name and initial, never the full name', () => {
  assert.equal(notifyName('Michael Thompson', 4810), 'Michael T.')
  assert.equal(notifyName('  olga   van  der berg ', 1), 'olga B.')
  assert.equal(notifyName('Ana', 2), 'Ana')
  assert.equal(notifyName('•••cu', 4810), '#4810')
  assert.equal(notifyName('', 77), '#77')
  assert.equal(notifyName(null, null), null)
})

test('a new chat carries the deadline; a new message does not', () => {
  assert.deepEqual(notificationText('new', 'Michael T.', 90), {
    title: 'New chat for you · Michael T.',
    body: 'Reply within 90 seconds, or the AI answers.',
  })
  assert.equal(notificationText('new', null, 120).title, 'New chat for you')
  assert.match(notificationText('new', null, 120).body, /120 seconds/)
  assert.deepEqual(notificationText('message', '#4810', 90), {
    title: 'New message · #4810',
    body: 'The client wrote in a chat you haven’t opened.',
  })
  for (const k of ['granted', 'default', 'denied', 'unsupported'] as const) assert.ok(PERMISSION_TEXT[k].text.length > 20)
})

test('the engine reads the settings at alert time, and the alert path never asks for permission', () => {
  const src = read('src/lib/notify-assignment.ts')
  const notifyBody = src.slice(src.indexOf('export function notifyAssignment'), src.indexOf('export function stopAssignmentAlerts'))
  assert.match(notifyBody, /readAlertPrefs\(\)/)
  assert.match(notifyBody, /const audible = prefs\.ring && prefs\.volume > 0/)
  assert.match(notifyBody, /if \(audible\) \{/)
  assert.match(notifyBody, /prefs\.flash && !_flashInterval/)
  assert.ok(!notifyBody.includes('requestPermission'))
  // The queue chime honours its own switch.
  assert.match(notifyBody, /if \(!prefs\.beep \|\| prefs\.volume <= 0\) return/)
  // A test alert never runs over a real one.
  assert.match(src, /export function testAlert[\s\S]*?if \(_alertsActive\) return null/)
  // The heartbeat names the chat, short.
  const hook = read('src/hooks/use-heartbeat.ts')
  assert.match(hook, /notifyName\(first\?\.visitor_name, first\?\.chat_number\)/)
  assert.match(hook, /kind: firstNewArrivalId \? 'new' : 'message'/)
})
