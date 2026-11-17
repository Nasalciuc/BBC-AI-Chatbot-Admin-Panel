// A person from the team who takes the chat over from Alex (17 Nov 2026). Their messages went out
// signed "Consultant" with a "C": the server published the bare row first, the signed copy came
// second, and the widget dropped it as a duplicate. And the header kept Alex's photo and name over a
// conversation a person was having. Names here are invented.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { CONSULTANT_LABEL, agentInitials, currentOperator, withOperatorFrom } from '../src/operator.ts'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')
const chat = readFileSync(join(SRC, 'ChatWindow.tsx'), 'utf8')
const top = readFileSync(join(SRC, 'PanelTop.tsx'), 'utf8')

const PHOTO = 'https://example.test/avatars/dana.png'
const ai = (id) => ({ id, role: 'ai', content: 'Alex here' })
const op = (id, name = 'Dana Reyes', photo = PHOTO) => ({ id, role: 'agent', content: 'Dana here', agent_name: name, agent_avatar_url: photo })
const note = (id) => ({ id, role: 'system', content: 'Dana Reyes joined the chat' })
const client = (id) => ({ id, role: 'user', content: 'ok' })

test('the header is Alex until a person writes', () => {
  assert.equal(currentOperator([]), null)
  assert.equal(currentOperator([ai('1'), client('2')]), null)
  // Joined is not answering: the note alone changes nothing.
  assert.equal(currentOperator([ai('1'), client('2'), note('3')]), null)
})

test('the person who wrote the latest reply is in the header, client lines and notes after it or not', () => {
  assert.deepEqual(currentOperator([ai('1'), note('2'), op('3')]), { name: 'Dana Reyes', avatarUrl: PHOTO })
  assert.deepEqual(currentOperator([ai('1'), op('2'), client('3'), note('4')]), { name: 'Dana Reyes', avatarUrl: PHOTO })
  // Another person took over: the latest one.
  assert.deepEqual(currentOperator([op('1'), op('2', 'Sam Ortiz', null)]), { name: 'Sam Ortiz', avatarUrl: null })
})

test('the header is Alex again when the chat comes back to him', () => {
  assert.equal(currentOperator([op('1'), client('2'), ai('3')]), null)
})

test('the latest reply by time, not by place: a late poll appends older rows at the end (CodeRabbit #264)', () => {
  const at = (row, iso) => ({ ...row, created_at: iso })
  // Dana wrote at 10:05; Alex's 10:01 reply came back from a late poll after it.
  assert.deepEqual(
    currentOperator([at(op('2'), '2026-11-17T10:05:00+00:00'), at(ai('1'), '2026-11-17T10:01:00Z')]),
    { name: 'Dana Reyes', avatarUrl: PHOTO },
  )
  // And the other way round: Alex is the latest even when Dana's older row lands last.
  assert.equal(currentOperator([at(ai('3'), '2026-11-17T10:09:00Z'), at(op('2'), '2026-11-17T10:05:00Z')]), null)
  // A row without a time keeps its place.
  assert.equal(currentOperator([at(op('2'), '2026-11-17T10:05:00Z'), ai('x')]), null)
})

test('a person with no name or photo: "Consultant" and the initial', () => {
  assert.deepEqual(currentOperator([op('1', '  ', '')]), { name: CONSULTANT_LABEL, avatarUrl: null })
  assert.deepEqual(currentOperator([{ id: '1', role: 'agent', content: 'hi' }]), { name: 'Consultant', avatarUrl: null })
  assert.equal(agentInitials('Dana Reyes'), 'DR')
  assert.equal(agentInitials('Consultant'), 'C')
})

test('a second copy of a row signs the bare one on screen', () => {
  const bare = { id: 'm1', role: 'agent', content: 'Hi, I am Dana' }
  const signed = { ...bare, agent_name: 'Dana Reyes', agent_avatar_url: PHOTO }
  assert.deepEqual(withOperatorFrom(bare, signed), signed)
  // Name without photo, then the photo.
  const named = { ...bare, agent_name: 'Dana Reyes', agent_avatar_url: null }
  assert.deepEqual(withOperatorFrom(named, signed), signed)
})

test('a copy never undoes what is on screen, and nothing to add means the same row', () => {
  const signed = op('m1')
  const bare = { id: 'm1', role: 'agent', content: 'Dana here' }
  assert.equal(withOperatorFrom(signed, bare), signed)
  assert.equal(withOperatorFrom(signed, op('m1', 'Someone Else', 'https://example.test/other.png')), signed)
  assert.equal(withOperatorFrom(signed, signed), signed)
  // Missing on both sides stays missing, as the same row.
  assert.equal(withOperatorFrom(bare, { ...bare, agent_avatar_url: null }), bare)
  // Alex's and the notes' rows are never signed.
  const row = ai('a1')
  assert.equal(withOperatorFrom(row, { ...row, agent_name: 'Dana Reyes' }), row)
})

test('the stream signs a duplicate instead of dropping it', () => {
  const generic = chat.slice(chat.indexOf("if (msg.role === 'user') return"), chat.indexOf('source.onerror'))
  assert.match(generic, /const merged = withOperatorFrom\(prev\[at\], msg\)/)
  assert.match(generic, /if \(at === -1\) return \[\.\.\.prev, msg\]/)
  assert.doesNotMatch(generic, /if \(prev\.some\(m => m\.id === msg\.id\)\) return prev/)
})

test('the header takes the person: photo or initials, no "AI" badge, their name and line', () => {
  assert.match(chat, /const operator = currentOperator\(shown\)/)
  assert.match(chat, /<PanelTop\s+titleId="bw-chat-title"\s+operator=\{operator\}/)
  const from = top.indexOf('function OperatorAvatar')
  const avatar = top.slice(from, top.indexOf('\n}\n', from))
  assert.match(avatar, /onError=\{\(\) => setBroken\(true\)\}/)
  assert.match(avatar, /agentInitials\(operator\.name\)/)
  assert.match(avatar, /<i class="bw-head__live" \/>/)
  assert.doesNotMatch(avatar, /bw-ai|badge/)
  assert.match(top, /export const operatorLine = `\$\{CONSULTANT_LABEL\} · \$\{brand\.name\}`/)
  // Alex keeps his badge when the header is his.
  assert.match(top, /<PersonaAvatar cls="bw-head__ava" badge>/)
})
