/**
 * Agent SSE frames are validated once, into a discriminated union.
 * Consumers must never see `unknown`.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'

import { parseAgentSseEvent } from '../src/lib/types.ts'

test('parseAgentSseEvent: each variant becomes a typed object; garbage is null', () => {
  const typing = parseAgentSseEvent({ event: 'typing', is_typing: 1, text: 'hi' })
  assert.deepEqual(typing, { event: 'typing', is_typing: true, text: 'hi' })

  const presence = parseAgentSseEvent({
    event: 'presence',
    widget_open: false,
    widget_presence: 'left',
  })
  assert.equal(presence && 'event' in presence && presence.event, 'presence')
  assert.equal((presence as { widget_open?: boolean }).widget_open, false)

  const chunk = parseAgentSseEvent({ event: 'stream_chunk', delta: 9 })
  assert.deepEqual(chunk, { event: 'stream_chunk', delta: '9' })

  const end = parseAgentSseEvent({
    event: 'stream_end',
    id: 'm1',
    role: 'ai',
    content: 'done',
    created_at: 't',
  })
  assert.equal(end && 'event' in end && end.event, 'stream_end')
  assert.equal((end as { id: string }).id, 'm1')

  const row = parseAgentSseEvent({
    id: 'm2',
    role: 'agent',
    content: 'hello',
    created_at: 't',
  })
  assert.ok(row && !('event' in row))
  assert.equal((row as { id: string }).id, 'm2')

  assert.equal(parseAgentSseEvent(null), null)
  assert.equal(parseAgentSseEvent({}), null)
  assert.equal(parseAgentSseEvent({ event: 'x' }), null)
})

test('parseAgentSseEvent: an unknown event with an id is not a message row', () => {
  assert.equal(parseAgentSseEvent({ event: 'x', id: 'm1' }), null)

  const row = parseAgentSseEvent({ id: 'm1', role: 'agent', content: 'hi' })
  assert.ok(row && !('event' in row))
  assert.equal((row as { id: string }).id, 'm1')
  assert.equal((row as { content: string }).content, 'hi')

  const end = parseAgentSseEvent({
    event: 'stream_end',
    id: 'm1',
    role: 'ai',
    content: 'done',
    created_at: 't',
  })
  assert.equal(end && 'event' in end && end.event, 'stream_end')
  assert.equal((end as { id: string }).id, 'm1')
})
