// What the redesign (#237, #238 — 9 Nov 2026) dropped and QA used to see:
// "Lead Created" under the chat, who had the chat at a glance, the operator
// history open, the agent on the chat's facts and on Closed rows, and the model
// of each AI reply in Leads. Pinned on the pure helpers and on the wiring.
// Runs with: node --experimental-strip-types --test tests/agent-trail.test.ts

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Conversation, Lead, Message } from '../src/lib/types.ts'
import { agentOf, closedByLine, crmView, leadLine, modelLabel, threadLines } from '../src/features/chats/view.ts'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (p: string) => readFileSync(join(root, p), 'utf-8')

const ME = 'me-1'

function conv(over: Partial<Conversation> = {}): Conversation {
  return {
    id: 'c1', tunnel: 'sales', mode: 'human', status: 'closed', visitor_name: 'Michael Turner',
    visitor_email: 'm@x.com', visitor_phone: '+1 555 0110', assigned_agent_id: 'a-2', assigned_agent_name: 'Ana Test',
    engaged_agent_name: null, agent_state: 'active', message_count: 4, ai_cost_total: 0,
    created_at: '2026-11-10T12:00:00Z', updated_at: '2026-11-10T12:30:00Z', closed_at: '2026-11-10T12:30:00Z', metadata: {}, ...over,
  } as Conversation
}

function lead(over: Partial<Lead> = {}): Lead {
  return {
    id: 'L1', conversation_id: 'c1', trip_type: 'round_trip', cabin_class: 'business', passengers: 2,
    origin_code: 'JFK', destination_code: 'LHR', departure_date: '2026-11-16', return_date: '2026-11-23',
    score: 60, tier: 'silver', status: 'new', intent_signals: {}, notes: '', created_in_crm: false, crm_lead_id: null,
    visitor_name: 'Michael Turner', visitor_email: 'm@x.com', visitor_phone: '+1 555 0110', ...over,
  } as Lead
}

const crmOf = (c: Conversation, l: Lead | null) => crmView(c, l, { canAct: true, ownerName: c.assigned_agent_name ?? null })

test('under the chat: a lead says "Lead created", and its CRM number once sent', () => {
  const c = conv()
  assert.equal(leadLine(null, crmOf(c, null)), null, 'no lead, no line')
  const sent = lead({ created_in_crm: true, crm_lead_id: '208131' })
  assert.deepEqual(leadLine(sent, crmOf(c, sent)), { head: 'In the CRM · lead 208131', tail: '', done: true })
  const open = lead()
  assert.deepEqual(leadLine(open, crmOf(c, open)), { head: 'Lead created', tail: 'not in the CRM yet', done: false })
  assert.deepEqual(leadLine(open, crmOf(c, open), true), { head: 'Lead created', tail: 'sending to the CRM…', done: false })
  const quiet = conv({ tag: 'abandoned' })
  assert.deepEqual(leadLine(open, crmOf(quiet, open)), { head: 'Lead created', tail: 'not sent to the CRM', done: false })
})

test('the line sits under the messages on every screen, before the reply box', () => {
  const src = read('src/features/chats/detail.tsx')
  assert.match(src, /const leadNow = leadLine\(lead, crm, markingLead\)/)
  const footer = src.slice(src.indexOf("{/* Footer: what's next */}"), src.indexOf('{canWrite && leftMin !== null && ('))
  assert.match(footer, /\{leadNow && \(/, 'the lead line opens the footer, outside the details panel')
  assert.match(footer, /<strong className=\{leadNow\.done \? 'text-k-0a6b35' : 'text-k-202020'\}>\{leadNow\.head\}<\/strong>/)
})

test('who had the chat, at a glance: the history line in the header for QA, supervisors and admins', () => {
  const src = read('src/features/chats/detail.tsx')
  const header = src.slice(src.indexOf('{/* Header */}'), src.indexOf('{canWrite && (\n            <CloseChat'))
  assert.match(header, /\{canViewHistory && <OperatorBadge conversationId=\{conversationId\} \/>\}/)
  assert.match(src, /const canViewHistory = \['owner', 'admin', 'dev', 'supervisor', 'qa'\]\.includes\(role\)/)
  // One request for the line and the history: the same query.
  const oh = read('src/features/chats/operator-history.tsx')
  assert.equal(oh.match(/queryKey: \['op-history', conversationId\]/g)?.length, 2)
  assert.match(oh, /if \(!canViewHistory \|\| !data\?\.summary\) return null/)
})

test('Closed rows say who had each chat', () => {
  assert.deepEqual(closedByLine(conv(), ME), { text: 'Ana Test', cls: 'text-k-444444' })
  assert.deepEqual(closedByLine(conv({ assigned_agent_id: ME }), ME), { text: 'You', cls: 'text-k-0a6b35' })
  const back = conv({ mode: 'ai', assigned_agent_id: null, assigned_agent_name: null, engaged_agent_name: 'Bogdan Test', agent_state: 'fallback', metadata: { engaged_agent_id: 'a-3' } })
  assert.deepEqual(closedByLine(back, ME), { text: 'Bogdan Test → assistant', cls: 'text-k-4a36c4' })
  assert.equal(closedByLine({ ...back, metadata: { engaged_agent_id: ME } }, ME)?.text, 'You → assistant')
  const ai = conv({ mode: 'ai', assigned_agent_id: null, assigned_agent_name: null, agent_state: 'ai_only' })
  assert.deepEqual(closedByLine(ai, ME), { text: 'Only the AI', cls: 'text-k-4a36c4' })
  assert.equal(closedByLine(conv({ assigned_agent_id: null, assigned_agent_name: null, agent_state: 'ai_only' }), ME), null, 'a person-mode chat with nobody on it says nothing')
  const list = read('src/features/chats/index.tsx')
  assert.match(list, /: activeTab === 'my_closed' \|\| activeTab === 'all_closed'\s+\? closedByLine\(conv, meId\)/)
})

test('the chat facts name the agent, and "Who handled it" opens with the chat', () => {
  assert.equal(agentOf(conv()), 'Ana Test')
  assert.equal(agentOf(conv({ assigned_agent_id: null, assigned_agent_name: null, engaged_agent_name: 'Bogdan Test', agent_state: 'fallback' })), 'Bogdan Test → assistant')
  assert.equal(agentOf(conv({ assigned_agent_id: null, assigned_agent_name: null, agent_state: 'ai_only' })), null)
  const panel = read('src/features/chats/components/details-panel.tsx')
  assert.match(panel, /\.\.\.\(agent \? \[\{ label: 'Agent', value: agent \}\] : \[\]\)/)
  const closed = panel.match(/const closedByDefault: FoldKey\[\] = \[(.*)\]\n/)?.[1] ?? ''
  assert.ok(closed.includes("'about'") && !closed.includes("'history'"), closed)
  // "This chat" (with the Agent row) opens for the same roles; agents keep it folded below "Wider chat".
  assert.match(closed, /\.\.\.\(p\.wide \|\| p\.canViewHistory \? \[\] : \(\['chat'\] as FoldKey\[\]\)\)/)
  // Still only for the roles that may read it.
  assert.match(panel, /\{p\.canViewHistory && \(\s*<Fold id='history'/)
})

test('Leads: each AI reply says which model wrote it', () => {
  const m = (id: string, role: Message['role'], model: string | null): Message =>
    ({ id, conversation_id: 'c1', role, content: 'x', model_used: model, cost: 0, created_at: '2026-11-10T12:00:00Z' }) as Message
  const lines = threadLines([m('1', 'user', null), m('2', 'ai', 'sonnet'), m('3', 'ai', 'template'), m('4', 'agent', null), m('5', 'system', 'x'), m('6', 'ai', null)])
  assert.deepEqual(lines.map((l) => l.model), [null, 'sonnet', 'template', null, null, null])
  assert.equal(modelLabel('template'), 'template · $0')
  assert.equal(modelLabel('sonnet'), 'sonnet')
  assert.equal(modelLabel('claude-opus-4-8'), 'claude-opus-4-8')
  assert.equal(modelLabel(null), '')
  const panel = read('src/features/leads/components/lead-panel.tsx')
  assert.match(panel, /\{l\.model && <span className='mt-0\.5 text-\[11px\] text-k-6b6b6b'>\{modelLabel\(l\.model\)\}<\/span>\}/)
})

test('the history line follows a take, a reply and a reassignment (review on #253)', () => {
  const src = read('src/features/chats/detail.tsx')
  const claim = src.slice(src.indexOf('const handleClaim = async'), src.indexOf('const handleCloseConfirm = async'))
  assert.match(claim, /\/claim`, \{ method: 'POST' \}\)\s+queryClient\.invalidateQueries\(\{ queryKey: \['conversation', conversationId\] \}\)\s+\/\/[^\n]*\n\s+queryClient\.invalidateQueries\(\{ queryKey: \['op-history', conversationId\] \}\)/)
  const send = src.slice(src.indexOf('const handleSend = async'), src.indexOf('const handleClaim = async'))
  const sent = send.slice(send.indexOf('await sendAgentMessage'), send.indexOf('} catch'))
  assert.match(sent, /queryClient\.invalidateQueries\(\{ queryKey: \['op-history', sentFor\] \}\)/, 'only after a send that worked')
  assert.equal(src.match(/onReassigned=\{\(\) => \{\s+queryClient\.invalidateQueries\(\{ queryKey: \['conversation', conversationId\] \}\)\s+queryClient\.invalidateQueries\(\{ queryKey: \['op-history', conversationId\] \}\)/g)?.length, 2, 'both "Give to…" panels')
})

test('"Who handled it" never says "the assistant" while loading, after a failed read, or for an agent who wrote', () => {
  const oh = read('src/features/chats/operator-history.tsx')
  const history = oh.slice(oh.indexOf('export function OperatorHistory'))
  const loading = history.indexOf('if (isLoading) return')
  const failed = history.indexOf('if (isError || !data) {')
  const wrote = history.indexOf("if (data.summary && !data.summary.startsWith('🤖')) {")
  const aiOnly = history.indexOf('The assistant handled the whole chat')
  assert.ok(loading > 0 && failed > loading && wrote > failed && aiOnly > wrote, { loading, failed, wrote, aiOnly })
  assert.match(history, /<span>\{data\.summary\} · wrote in the chat, no take was logged<\/span>/)
})
