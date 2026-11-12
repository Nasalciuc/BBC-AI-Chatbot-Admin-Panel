/**
 * Presence v2 (12 Nov 2026): what the widget tells the server about the visitor being on
 * the site. The policy is pure (presence-policy.ts); the wiring in ChatWindow.tsx is pinned
 * on the source (no DOM in these tests). The server's rule that reads the signals is
 * bbc-chatbot-api/app/services/client_presence.py.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  ACTIVITY_EVENTS, BEAT_IDLE_STOP_MS, BEAT_MS, idleSeconds, isMobileDevice, shouldBeat, signalQuery,
} from '../src/presence-policy.ts'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')
const chat = readFileSync(join(SRC, 'ChatWindow.tsx'), 'utf8')

test('a beat every 30 s, and none after 20 minutes nobody touched the page', () => {
  assert.equal(BEAT_MS, 30_000)
  assert.equal(shouldBeat(true, 1_000_000, 1_000_000), true)
  assert.equal(shouldBeat(true, 1_000_000 + BEAT_IDLE_STOP_MS, 1_000_000), true)
  assert.equal(shouldBeat(true, 1_000_000 + BEAT_IDLE_STOP_MS + 1, 1_000_000), false)
  // Out of view: never (the `hidden` signal said so, the server counts from it).
  assert.equal(shouldBeat(false, 1_000_000, 1_000_000), false)
})

test('idle is whole seconds since the last touch, never negative', () => {
  assert.equal(idleSeconds(10_999, 1_000), 9)
  assert.equal(idleSeconds(1_000, 5_000), 0)
})

test('the query carries the version, the signal, the idle seconds, the device, and for a beat the chat', () => {
  const beat = new URLSearchParams(signalQuery('beat', { chatOpen: false, idleS: 42.7, mobile: true }))
  assert.deepEqual(Object.fromEntries(beat), { v: '2', kind: 'beat', idle: '42', m: '1', chat: 'closed' })
  const hidden = new URLSearchParams(signalQuery('hidden', { chatOpen: true, idleS: 0, mobile: false }))
  assert.deepEqual(Object.fromEntries(hidden), { v: '2', kind: 'hidden', idle: '0', m: '0' })
  assert.equal(new URLSearchParams(signalQuery('leaving', { chatOpen: true, idleS: -5, mobile: false })).get('idle'), '0')
  // No idle at all: nothing claimed about activity (the server counts a beat as activity now).
  const blind = new URLSearchParams(signalQuery('beat', { chatOpen: true, idleS: null, mobile: false }))
  assert.equal(blind.has('idle'), false)
  assert.equal(blind.get('chat'), 'open')
})

test('the embedded chat (CRM iframe) claims no idleness and its beats never stop for it', () => {
  // Review on #251: the frame cannot see the page around it; counting only its own
  // touches would read "left" for a visitor busy on the host page.
  assert.match(chat, /idleS: embedded \? null : idleSeconds\(Date\.now\(\), lastActivityRef\.current\),/)
  assert.match(chat, /if \(!embedded && !shouldBeat\(true, Date\.now\(\), lastActivityRef\.current\)\) \{/)
})

test('only a page in view starts beating, so coming into view beats at once', () => {
  // Review on #251: an interval started while hidden (mounted in a background tab, or a
  // pageshow without being shown) made the visible transition wait for the next tick.
  assert.match(chat, /const startPing = \(\) => \{\s*if \(pingInterval \|\| document\.visibilityState !== 'visible'\) return/)
})

test('phones and tablets are told apart from computers', () => {
  const iphone = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148'
  const android = 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/131.0 Mobile Safari/537.36'
  const androidTablet = 'Mozilla/5.0 (Linux; Android 14; SM-X710) AppleWebKit/537.36 Chrome/131.0 Safari/537.36'
  const ipadDesktopSite = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/18.1 Safari/605.1.15'
  const windows = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36'
  assert.equal(isMobileDevice({ userAgent: iphone }), true)
  assert.equal(isMobileDevice({ userAgent: android }), true)
  assert.equal(isMobileDevice({ userAgent: androidTablet }), true)
  assert.equal(isMobileDevice({ userAgent: ipadDesktopSite, maxTouchPoints: 5 }), true)
  assert.equal(isMobileDevice({ userAgent: ipadDesktopSite, maxTouchPoints: 0 }), false)   // a Mac
  assert.equal(isMobileDevice({ userAgent: windows, maxTouchPoints: 10 }), false)          // a touch laptop
  assert.equal(isMobileDevice({ userAgent: windows, userAgentData: { mobile: true } }), true)
  assert.equal(isMobileDevice(undefined), false)
})

test('activity is anything the visitor does on the page, not only in the chat', () => {
  for (const e of ['pointerdown', 'pointermove', 'keydown', 'wheel', 'scroll', 'touchstart']) {
    assert.ok(ACTIVITY_EVENTS.includes(e), e)
  }
  assert.match(chat, /for \(const e of ACTIVITY_EVENTS\) window\.addEventListener\(e, on, opts\)/)
  assert.match(chat, /const opts: AddEventListenerOptions = \{ capture: true, passive: true \}/)
  assert.match(chat, /for \(const e of ACTIVITY_EVENTS\) window\.removeEventListener\(e, on, opts\)/)
})

test('the three signals go to /ping, and a beat goes out with the chat open or closed', () => {
  assert.match(chat, /apiFetch\(`\$\{apiUrl\}\/api\/chat\/session\/\$\{convId\}\/ping\?\$\{q\}`, \{ method: 'POST', keepalive \}\)/)
  const ping = chat.slice(chat.indexOf('const sendPing = () => {'), chat.indexOf('const startPing = () => {'))
  assert.match(ping, /if \(document\.visibilityState !== 'visible'\) return/)
  assert.doesNotMatch(ping, /visibleRef/, 'the chat being closed does not stop the beats')
  assert.match(ping, /signal\('beat'\)/)
})

test('out of view says `hidden` at once; back in view beats at once', () => {
  const vis = chat.slice(chat.indexOf('const handleVisibility = () => {'))
  assert.match(vis.slice(0, 300), /startPing\(\)\s*\} else \{\s*stopPing\(\)\s*signal\('hidden', true\)/)
})

test('pagehide says `leaving` once, with keepalive; beforeunload is gone (it can keep a page out of the back-forward cache)', () => {
  assert.match(chat, /window\.addEventListener\('pagehide', handlePageLeave\)/)
  const leave = chat.slice(chat.indexOf('const handlePageLeave = () => {'))
  assert.match(leave.slice(0, 200), /if \(leaveSent\) return\s+leaveSent = true\s+stopPing\(\)\s+signal\('leaving', true\)/)
  assert.doesNotMatch(chat, /beforeunload/)
  assert.doesNotMatch(chat, /notifySessionClose\('left'/)
  // Back from the back-forward cache: beats again and can say `leaving` again.
  const back = chat.slice(chat.indexOf('const handlePageReturn = () => {'))
  assert.match(back.slice(0, 120), /leaveSent = false\s+startPing\(\)/)
})

test('the beats stopped by 20 idle minutes start again on the first touch', () => {
  assert.match(chat, /onActivityRef\.current = \(\) => \{ if \(beatsPaused\) sendPing\(\) \}/)
  assert.match(chat, /lastActivityRef\.current = Date\.now\(\)\s+onActivityRef\.current\(\)/)
  // Unmounted: nothing left pointing at a dead conversation.
  assert.match(chat, /beatNowRef\.current = \(\) => \{\}\s+onActivityRef\.current = \(\) => \{\}/)
})
