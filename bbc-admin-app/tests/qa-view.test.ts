// The QA page (src/features/qa/view.ts): the day it opens on. Server half: bbc-chatbot-api app/services/email.py
// (the morning e-mail links to /qa?day=YYYY-MM-DD) and app/services/qa_sample.py.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dayFromLink, yesterdayInNewYork } from '../src/features/qa/view.ts'

const NOW = new Date('2026-11-22T15:00:00Z')

test('the e-mail’s day opens, not the day before the one it is read on (CodeRabbit #273)', () => {
  assert.equal(dayFromLink('2026-11-20', NOW), '2026-11-20')
  assert.equal(yesterdayInNewYork(NOW), '2026-11-21')
  const mail = readFileSync(new URL('../../bbc-chatbot-api/app/services/email.py', import.meta.url), 'utf8')
  assert.match(mail, /\/qa\?day=\{day\}/, 'the link the page reads')
})

test('no day, or one that is not a calendar day, opens yesterday in New York', () => {
  for (const bad of [undefined, null, '', '2026-11', '2026-02-30', '2026-13-01', '20261120', 20261120, '2026-11-20T00:00', ' 2026-11-20']) {
    assert.equal(dayFromLink(bad, NOW), '2026-11-21', String(bad))
  }
})

test('the page starts from the link’s day', () => {
  const page = readFileSync(new URL('../src/features/qa/index.tsx', import.meta.url), 'utf8')
  assert.match(page, /useState\(\(\) => dayFromLink\(search\.day\)\)/)
})
