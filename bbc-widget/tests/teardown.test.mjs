/**
 * A page that mounts the widget and leaves it without a reload (the panel's app routes) must be able to unmount it:
 * removing #bbc-widget-root alone runs no effect cleanup, so a phone sheet would keep the page from scrolling and the
 * chat's stream and timers would keep running. The bundle exposes __BBC_WIDGET_TEARDOWN__ for that.
 *
 * Plain .mjs reading the source: the widget's zero-dep test pattern.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const entry = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'entry.tsx'), 'utf8')

test('the bundle exposes a teardown that unmounts before it removes the root', () => {
  const at = entry.indexOf('__BBC_WIDGET_TEARDOWN__ = () => {')
  assert.ok(at > 0, 'no teardown exposed')
  const body = entry.slice(at, entry.indexOf('}', at))
  const unmount = body.indexOf('render(null, root)')
  const remove = body.indexOf('root.remove()')
  assert.ok(unmount > 0 && remove > unmount, 'unmount first: removing the root alone runs no cleanup')
  // After it, the page can load the widget again.
  assert.match(body, /__BBC_WIDGET_LOADED__ = false/)
  assert.match(body, /__BBC_WIDGET_TEARDOWN__ = undefined/)
})

test('the teardown is set after the widget is rendered into its root', () => {
  assert.ok(entry.indexOf('render(<Widget') < entry.indexOf('__BBC_WIDGET_TEARDOWN__ = () => {'))
})
