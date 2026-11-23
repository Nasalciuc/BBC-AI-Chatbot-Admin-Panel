// Settings › Alerts — pinned on lib/alert-prefs.ts and the alert engine.
// Runs with: node --experimental-strip-types --test tests/alert-prefs.test.ts

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  ALERT_PREFS_VERSION,
  DEFAULT_ALERT_PREFS,
  MELODY_REPEAT_MS,
  PERMISSION_TEXT,
  SOUND_LABEL,
  notificationText,
  notifyName,
  parseAlertPrefs,
  ringRepeatMs,
  volumeLow,
} from '../src/lib/alert-prefs.ts'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (p: string) => readFileSync(join(root, p), 'utf-8')

test('untouched settings play the gentle melody everywhere: full volume, flashing tab, queue chime', () => {
  assert.deepEqual(DEFAULT_ALERT_PREFS, { ring: true, flash: true, sound: 'melody', volume: 100, beep: true })
  assert.deepEqual(parseAlertPrefs(null), DEFAULT_ALERT_PREFS)
  assert.deepEqual(parseAlertPrefs(''), DEFAULT_ALERT_PREFS)
  assert.equal(SOUND_LABEL.melody, 'Gentle melody')
  assert.equal(ringRepeatMs('melody'), MELODY_REPEAT_MS)
  assert.ok(MELODY_REPEAT_MS >= 4000, 'the melody (about 3.5 s) never runs into itself')
  assert.equal(ringRepeatMs('soft'), 1800)
})

test('the phone ring kept as the old default reads as the melody; one chosen since stays', () => {
  // Saved before the melody (no version): "Phone ring" was only the default of the day.
  assert.equal(parseAlertPrefs('{"ring":true,"sound":"classic","volume":60}').sound, 'melody')
  assert.equal(parseAlertPrefs('{"sound":"classic","volume":60}').volume, 60, 'the rest is kept')
  // Saved since (every save writes the version): a choice.
  assert.equal(parseAlertPrefs(`{"sound":"classic","v":${ALERT_PREFS_VERSION}}`).sound, 'classic')
  // Rule 5: the other sounds were always a choice, versioned or not.
  assert.equal(parseAlertPrefs('{"sound":"soft"}').sound, 'soft')
  assert.equal(parseAlertPrefs('{"sound":"two"}').sound, 'two')
  // What is saved carries the version, so it reads back as written.
  const src = read('src/lib/alert-prefs.ts')
  assert.match(src, /localStorage\.setItem\(ALERT_PREFS_KEY, JSON\.stringify\(\{ \.\.\.p, v: ALERT_PREFS_VERSION \}\)\)/)
})

test('whatever is stored, a usable set comes back', () => {
  assert.deepEqual(parseAlertPrefs('not json'), DEFAULT_ALERT_PREFS)
  assert.deepEqual(parseAlertPrefs('[1,2]'), DEFAULT_ALERT_PREFS)
  assert.deepEqual(parseAlertPrefs('{"ring":false,"sound":"two","volume":45}'), { ...DEFAULT_ALERT_PREFS, ring: false, sound: 'two', volume: 45 })
  assert.equal(parseAlertPrefs('{"sound":"siren"}').sound, 'melody')
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

test('the melody everywhere: the ring repeats it until the chat is opened and stops it mid-phrase; the queue chime and the reminders play it once', () => {
  const src = read('src/lib/notify-assignment.ts')
  const ring = src.slice(src.indexOf('export function notifyAssignment'), src.indexOf('export function playQueueChime'))
  assert.match(ring, /_ringMelody = _playMade\(sound, volume\)/)
  assert.match(ring, /\}, ringRepeatMs\(sound\)\)/)
  // Each repeat quiets the phrase before it (CodeRabbit #273): with the audio context suspended, phrases wait at
  // its frozen clock, and one no longer tracked played when it resumed, after the chat was opened.
  assert.match(ring, /setInterval\(\(\) => \{[\s\S]*?_silence\(_ringMelody\)\n\s+_ringMelody = _playMade\(sound, volume\)\n\s+\}, ringRepeatMs\(sound\)\)/)
  const stop = src.slice(src.indexOf('export function stopAssignmentAlerts'), src.indexOf('// ── Settings › Alerts'))
  assert.match(stop, /_silence\(_ringMelody\)/)
  const chime = src.slice(src.indexOf('export function playQueueChime'), src.indexOf('export function notifyTaskReminder'))
  assert.match(chime, /_playOnce\(prefs\.sound, 0\.6 \* \(prefs\.volume \/ 100\)\)/)
  assert.doesNotMatch(chime, /notification\.wav/, 'the recording only for whoever chose it')
  const reminder = src.slice(src.indexOf('export function notifyTaskReminder'), src.indexOf('export function stopAssignmentAlerts'))
  assert.match(reminder, /_playOnce\(prefs\.sound, \(prefs\.sound === 'classic' \? 0\.7 : 1\) \* \(prefs\.volume \/ 100\)\)/)
  // Every beat with nothing waiting stops the ring: a one-shot never registers as the ring's, so it plays out.
  const once = src.slice(src.indexOf('function _playOnce'), src.indexOf('export function notifyAssignment'))
  assert.doesNotMatch(once, /_ringMelody/)
  // Settings: the melody first, and a row that wraps on a phone.
  const page = read('src/features/settings/alerts/index.tsx')
  assert.match(page, /const SOUNDS: RingSound\[\] = \['melody', 'classic', 'soft', 'two'\]/)
  assert.match(page, /role='radiogroup' aria-label='Ring sound' className='flex flex-wrap/)
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
