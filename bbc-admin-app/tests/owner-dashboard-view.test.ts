// What the owner's Dashboard says — pinned on the pure helpers in
// features/dashboard/owner/view.ts. Runs with:
// node --experimental-strip-types --test tests/owner-dashboard-view.test.ts

import { test } from 'node:test'
import assert from 'node:assert/strict'
import type { OwnerDashboard } from '../src/lib/types.ts'
import {
  agentsHeadline,
  attentionView,
  changesThisWeek,
  delta,
  hourLong,
  hourTick,
  overDeadlineText,
  overviewHeadline,
  qualityHeadline,
  phoneNote,
  rangeText,
  scopeText,
  secondsText,
  sitesText,
  TABS_FOR,
  sortAgents,
  sourceSentence,
  zoneText,
} from '../src/features/dashboard/owner/view.ts'

const cohort = (over: Partial<OwnerDashboard['overview']['now']> = {}) => ({
  started: 176, wrote: 122, contact_only: 54, ready: 39, ready_ai_only: 38, ready_with_agent: 1,
  ai_only: 62, ai_only_ready: 38, with_agent: 60, ...over,
})

const agentBlock = (over = {}) => ({
  given: 60, first_reply_median_s: 35, replies_measured: 54, replies_over_deadline: 6,
  went_back: 14, after_yes: 3, never_wrote: 4, ...over,
})

function dash(over: Partial<OwnerDashboard> = {}): OwnerDashboard {
  const hours = Array.from({ length: 24 }, (_, h) => ({ hour: h, given: 0, went_back: 0, ready_people: 0 }))
  hours[14] = { hour: 14, given: 8, went_back: 4, ready_people: 4 }
  hours[10] = { hour: 10, given: 3, went_back: 1, ready_people: 4 }
  return {
    days: 7, tz_offset_minutes: -300, now: '2026-11-06T17:00:00+00:00', range_start: '2026-10-31T04:00:00+00:00',
    previous: { start: '2026-10-24T04:00:00+00:00', end: '2026-10-30T17:00:00+00:00' },
    sites: ['Buy Business Class', 'Business Class Tickets'],
    overview: { now: cohort(), before: cohort({ ready: 34, ai_only: 60, ai_only_ready: 35 }), by_day: [] },
    funnel: { ai_only: [52, 46, 43, 41, 38], with_agent: [34, 25, 16, 0, 0], time_to_ready_median_s: 156 },
    leave: { total: 69, total_before: 60, rows: [] },
    sources: { source: [], site: [], device: [], page: [], route: [] },
    source_spellings: [],
    agents: {
      now: agentBlock(), before: agentBlock({ went_back: 18 }), by_hour: hours, ready_hours_before: {},
      by_agent: [
        { user_id: 'rohan', name: 'Rohan Mehta', role: 'sales', ready_hours: 36, chats: 11, first_reply_median_s: 74, first_reply_before_s: 61, went_back: 6, after_yes: 1, never_wrote: 2, not_in_crm: 2, not_in_crm_conversations: [] },
        { user_id: 'victor', name: 'Victor Rusu', role: 'sales', ready_hours: 34, chats: 14, first_reply_median_s: 27, first_reply_before_s: 31, went_back: 1, after_yes: 0, never_wrote: 0, not_in_crm: 0, not_in_crm_conversations: [] },
        { user_id: 'ana', name: 'Ana Ceban', role: 'sales', ready_hours: 31, chats: 12, first_reply_median_s: 35, first_reply_before_s: 33, went_back: 7, after_yes: 1, never_wrote: 1, not_in_crm: 1, not_in_crm_conversations: [] },
      ],
    },
    quality: {
      ready: 39, reviewed: 0, to_review: 39, waiting_long: 30, oldest_waiting_since: null,
      last_review_at: '2026-08-22T11:33:37+00:00', qa_accounts: 1, sent_to_phone: 9, sent_to_phone_conversations: [],
      ai_review: [], ai: { cost: 1.64, cost_per_ready: 0.04, answers: 400, template_answers: 68, latency_median_ms: 4700, latency_line_ms: 5000 },
      crm: { reached: 150, confirmed: 36, from_agent_chats: 0, defaults: 114, not_in_crm: 4, closed_by_agent: 3, refused: 1, refused_reasons: { email_typo_domain: 1 }, not_sent_yet: 0 },
    },
    attention: [
      { kind: 'agent', level: 'urgent', user_id: 'rohan', name: 'Rohan Mehta', went_back: 6, chats: 11, first_reply_s: 74, ready_hours: 36 },
      { kind: 'hour', level: 'check', hour: 14, went_back: 4, given: 8, ready_people: 4 },
    ],
    scope: { tunnel: 'all', team_scoped_links: false },
    ...over,
  }
}

test('numbers in words', () => {
  assert.equal(secondsText(35), '35 s')
  assert.equal(secondsText(156), '2.6 min')
  assert.equal(secondsText(null), '—')
  assert.equal(hourTick(8), '8a')
  assert.equal(hourTick(12), '12p')
  assert.equal(hourTick(14), '2p')
  assert.equal(hourLong(14), '2 pm')
  assert.equal(hourLong(12), 'noon')
  assert.equal(sitesText(['Buy Business Class', 'Business Class Tickets']), 'Buy Business Class and Business Class Tickets')
  assert.equal(zoneText('America/New_York'), 'New York time')
  assert.equal(overDeadlineText(6, 54), '1 in 9 took longer than 90 s.')
  assert.equal(overDeadlineText(0, 54), 'None took longer than 90 s.')
})

test('the period in the subtitle is the viewer’s days', () => {
  assert.equal(rangeText(dash()), 'last 7 days, 30 Oct – 6 Nov')
  assert.equal(rangeText({ ...dash(), days: 1, range_start: '2026-11-06T05:00:00+00:00' }), 'today, Fri 6 Nov')
})

test('deltas: the colour says whether the change is good', () => {
  assert.deepEqual(delta(39, 34, 7), { text: '▲ 5 vs the 7 days before', tone: 'good' })
  assert.deepEqual(delta(14, 18, 7, { upIsGood: false }), { text: '▼ 4 vs the 7 days before', tone: 'good' })
  assert.deepEqual(delta(14, 10, 1, { upIsGood: false }), { text: '▲ 4 vs yesterday at this time', tone: 'bad' })
  assert.deepEqual(delta(61, 58, 7, { unit: ' pts' }), { text: '▲ 3 pts vs the 7 days before', tone: 'good' })
  assert.equal(delta(5, 5, 30)?.tone, 'same')
})

test('the overview headline names the weak spot', () => {
  assert.equal(
    overviewHeadline(dash()),
    'Ready leads are up 15% on the 7 days before. The weak spot is the agents: 14 chats went back to the assistant, most at 2 pm.'
  )
  assert.equal(overviewHeadline(dash({ attention: [] })), 'Ready leads are up 15% on the 7 days before. Nothing crossed a line.')
  const none = dash({ overview: { now: cohort({ ready: 0 }), before: cohort({ ready: 0 }), by_day: [] }, attention: [] })
  assert.equal(overviewHeadline(none), 'No ready leads in this period. Nothing crossed a line.')
})

test('needs attention never guesses a gender and says where to look', () => {
  const a = attentionView(dash().attention[0], 1)
  assert.equal(a.title, '6 of Rohan Mehta’s 11 chats went back to the assistant.')
  assert.equal(a.sub, 'First reply: 74 s. Ready 36 h.')
  assert.ok(!/\b(his|her|he|she)\b/i.test(`${a.title} ${a.sub}`))
  const h = attentionView(dash().attention[1], 1)
  assert.equal(h.title, 'At 2 pm, 4 of 8 chats went back to the assistant.')
  assert.equal(h.action.to, 'hour')
  const r = attentionView({ kind: 'review', level: 'check', waiting: 39, waiting_long: 30, last_review_at: '2026-08-22T10:00:00Z' }, 1)
  assert.equal(r.title, '39 ready leads are waiting for review.')
  assert.match(r.sub, /^Nobody has reviewed one since 22 Aug\. 1 QA account\./)
})

test('agents headline: whose chats went back, and the rest', () => {
  assert.equal(
    agentsHeadline(dash()),
    '6 of the 14 chats that went back to the assistant were Rohan Mehta’s. Ready the most hours (36 h), but a first reply of 74 s. The other 8 are spread across 2 people.'
  )
  assert.equal(agentsHeadline(dash({ attention: [] })), '14 chats went back to the assistant. Nobody is past the line.')
})

test('quality headline starts from the last review', () => {
  assert.equal(qualityHeadline(dash()), 'Nobody has reviewed a ready lead since 22 Aug. 39 are waiting in this period. Start with the oldest.')
})

test('what to change: fixed rules, at most two', () => {
  const chats = [{ id: 'c1', first_name: 'Ana', chat_number: 1 }]
  const leave = {
    total: 69, total_before: 60,
    rows: [
      { key: 'from' as const, label: '', chats: 24, before: 18, conversations: chats },
      { key: 'other' as const, label: '', chats: 16, before: 16, conversations: chats },
      { key: 'specialist' as const, label: '', chats: 6, before: 2, conversations: chats },
      { key: 'dates' as const, label: '', chats: 7, before: 7, conversations: chats },
    ],
  }
  const c = changesThisWeek(leave, 7)
  assert.equal(c.length, 2)
  assert.equal(c[0].title, 'Ask where they fly to first, then where from.')
  assert.equal(
    c[0].text,
    '24 of the 69 people who left did it right after “Where are you flying from?”, 6 more than the 7 days before. The destination is the question people came with.'
  )
  assert.equal(c[1].title, 'Don’t promise a person when nobody is Ready.')
  // Under a fifth: nothing from rule one.
  assert.equal(changesThisWeek({ ...leave, rows: [{ ...leave.rows[0], chats: 10 }] }, 7).length, 0)
})

test('sources: best and worst among sources with 5+ writers', () => {
  const rows = [
    { name: 'Kayak', chats: 52, wrote: 36, ready: 13 },
    { name: 'Google', chats: 18, wrote: 13, ready: 6 },
    { name: 'No source', chats: 35, wrote: 23, ready: 7 },
    { name: 'Instagram', chats: 8, wrote: 4, ready: 3 },
  ]
  assert.equal(sourceSentence(rows, 'source'), 'Google turns the most writers into ready leads (46%). No source the fewest (30%).')
})

test('agents sort worst first', () => {
  const rows = dash().agents.by_agent
  assert.deepEqual(sortAgents(rows, 'went_back').map((r) => r.user_id), ['ana', 'rohan', 'victor'])
  assert.deepEqual(sortAgents(rows, 'ready_hours').map((r) => r.user_id), ['ana', 'victor', 'rohan'])
  assert.deepEqual(sortAgents(rows, 'first_reply_median_s').map((r) => r.user_id), ['rohan', 'ana', 'victor'])
})

test('each role gets its tabs and its scope line', () => {
  assert.deepEqual(TABS_FOR.owner, ['overview', 'conversion', 'agents', 'quality'])
  assert.deepEqual(TABS_FOR.team, ['overview', 'agents'])
  assert.deepEqual(TABS_FOR.qa, ['quality', 'conversion', 'agents'])
  assert.equal(scopeText(dash(), 'owner'), 'Buy Business Class and Business Class Tickets · all chats')
  assert.equal(scopeText(dash({ scope: { tunnel: 'sales', team_scoped_links: true } }), 'team'), 'All sales chats')
  assert.equal(scopeText(dash({ scope: { tunnel: 'all', team_scoped_links: true } }), 'team'), 'All chats')
  assert.equal(scopeText(dash(), 'qa'), 'All chats')
  assert.equal(phoneNote('owner'), 'Conversion, Agents and Quality open on a computer.')
  assert.equal(phoneNote('team'), 'Agents opens on a computer.')
  assert.equal(phoneNote('qa'), 'Conversion and Agents open on a computer.')
})
