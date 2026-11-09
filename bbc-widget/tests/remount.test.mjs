/**
 * The widget mounts again after __BBC_WIDGET_TEARDOWN__.
 *
 * CodeRabbit #229: the bundle defined window.__bbcWidgetRoutes with
 * Object.defineProperty (non-configurable). The teardown resets the loaded
 * flag, so a remount runs the bundle again, and the second definition threw
 * a TypeError before anything mounted, leaving the flag true for good.
 * Runs with: node --experimental-strip-types --test tests/remount.test.mjs
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { exposeRoutes } from '../src/page-globals.ts'

const entry = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'entry.tsx'), 'utf8')

test('the route map is defined once, and a second run of the bundle does not throw', () => {
  const page = {}
  exposeRoutes(page, { transcriptExportV0: '/api/chat/v0/transcript-export' })
  assert.doesNotThrow(() => exposeRoutes(page, { transcriptExportV0: '/api/chat/v0/transcript-export' }))
  assert.equal(page.__bbcWidgetRoutes.transcriptExportV0, '/api/chat/v0/transcript-export')
  assert.ok(Object.isFrozen(page.__bbcWidgetRoutes))
  assert.equal(Object.keys(page).includes('__bbcWidgetRoutes'), false, 'not enumerable')
})

test('the old definition, run twice, is what threw', () => {
  const page = {}
  const define = () => Object.defineProperty(page, '__bbcWidgetRoutes', { value: Object.freeze({}), enumerable: false })
  define()
  assert.throws(define, TypeError)
})

test('the bundle uses exposeRoutes and keeps the route constant in it', () => {
  assert.match(entry, /exposeRoutes\(window, \{ transcriptExportV0: TRANSCRIPT_EXPORT_V0 \}\)/)
  assert.doesNotMatch(entry, /Object\.defineProperty\(window, '__bbcWidgetRoutes'/)
  // Teardown still lets the page load it again.
  assert.match(entry, /__BBC_WIDGET_LOADED__ = false/)
})
