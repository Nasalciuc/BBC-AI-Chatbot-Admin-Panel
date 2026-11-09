import { Link } from '@tanstack/react-router'
import { Check, TriangleAlert } from 'lucide-react'
import type { OwnerDashboard } from '@/lib/types'
import { cn } from '@/lib/utils'
import { gateReasonText } from '@/features/chats/view'
import { AttentionList, Headline, OpenChats, Panel, StatCard, linkCls } from './bits'
import { AI_REVIEW_TEXT, attentionView, money, pct, plural, qualityHeadline, shortDate, type AttentionView } from './view'

function leadsLink(view: 'not_in_crm' | 'to_review', label: string) {
  return (
    <Link to='/leads' search={{ view } as never} className={linkCls}>
      {label}
    </Link>
  )
}

function Line({ label, value, cls, action, sub = false }: { label: string; value: React.ReactNode; cls?: string; action?: React.ReactNode; sub?: boolean }) {
  return (
    <li className={cn('flex items-center gap-3 border-t border-k-ececec py-2.5 text-sm', sub && 'ps-3.5 text-k-555555')}>
      <span className={cn('min-w-0 flex-1', cls)}>{label}</span>
      <strong className={cn('text-k-202020', cls)}>{value}</strong>
      {action && <span className='w-[78px] text-right'>{action}</span>}
    </li>
  )
}

export function Quality({ d, go }: { d: OwnerDashboard; go: (to: AttentionView['action']['to']) => void }) {
  const q = d.quality
  const crm = q.crm
  const ai = q.ai
  const items = d.attention.filter((x) => x.kind === 'review' || x.kind === 'ai_latency').map((x) => attentionView(x, q.qa_accounts))
  const slow = ai.latency_median_ms !== null && ai.latency_median_ms > ai.latency_line_ms
  const reviewedPct = pct(q.reviewed, q.ready)
  return (
    <div className='flex flex-col gap-[18px]'>
      <Headline text={qualityHeadline(d)} />
      <div className='grid grid-cols-1 gap-3.5 sm:grid-cols-2 xl:grid-cols-4'>
        <StatCard label='Ready leads to review' value={String(q.to_review)} tint='bg-k-fdf6e3' action={q.to_review > 0 ? leadsLink('to_review', 'Start reviewing') : undefined}>
          {q.last_review_at ? `Nobody has reviewed a ready lead since ${shortDate(q.last_review_at)}.` : 'No ready lead has been reviewed yet.'}
        </StatCard>
        <StatCard label='Reviewed' value={reviewedPct === null ? '—' : `${reviewedPct}%`}>
          {q.reviewed} of {plural(q.ready, 'ready lead')} in this period. {plural(q.qa_accounts, 'QA account')}.
        </StatCard>
        <StatCard
          label='Sent to the phone'
          value={String(q.sent_to_phone)}
          action={<OpenChats chats={q.sent_to_phone_conversations} count={q.sent_to_phone} label='Open them' />}
        >
          The chat ended with “call +1 888…”, and the client left.
        </StatCard>
        <StatCard label='Not in the CRM' value={String(crm.not_in_crm)} action={crm.not_in_crm > 0 ? leadsLink('not_in_crm', 'See them') : undefined}>
          {crm.not_in_crm === 0
            ? 'Every chat with a phone reached the CRM.'
            : `${crm.closed_by_agent} closed by an agent without sending, ${crm.refused} refused before the CRM${crm.not_sent_yet ? `, ${crm.not_sent_yet} not sent yet` : ''}.`}
        </StatCard>
      </div>
      <Panel title='Needs attention'>
        <AttentionList items={items} onAction={go} note='Shown only past a line: a ready lead waiting more than 2 days for review, an AI answer over 5 s.' />
      </Panel>
      <div className='grid grid-cols-1 gap-[18px] xl:grid-cols-2'>
        <div className='flex flex-col gap-[18px]'>
          <Panel title='The AI, for review' hint='Sorted by the people lost.'>
            <table className='w-full border-collapse'>
              <thead>
                <tr>
                  <th className='py-2 text-left text-[12px] font-bold text-k-555555'>What happened</th>
                  <th className='w-16 py-2 text-right text-[12px] font-bold text-k-555555'>Chats</th>
                  <th className='w-24 py-2 text-right text-[12px] font-bold text-k-555555'>Left after it</th>
                  <th className='w-16' />
                </tr>
              </thead>
              <tbody>
                {q.ai_review.map((r) => (
                  <tr key={r.key} className='border-t border-k-ececec'>
                    <td className='py-2.5 pe-2'>
                      <span className={cn('block text-sm', r.key === 'repeat' && r.chats > 0 ? 'text-k-8a4b00' : 'text-k-202020')}>{AI_REVIEW_TEXT[r.key].title}</span>
                      <span className='block text-xs text-k-6b6b6b'>{AI_REVIEW_TEXT[r.key].text}</span>
                    </td>
                    <td className='py-2.5 text-right text-sm text-k-202020'>{r.chats}</td>
                    <td className='py-2.5 text-right text-sm font-bold text-k-202020'>{r.left_after}</td>
                    <td className='py-2.5 text-right'>
                      <OpenChats chats={r.conversations} count={r.left_after || r.chats} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>
          <Panel title='CRM'>
            <ul className='m-0 flex list-none flex-col p-0'>
              <Line label='Reached the CRM' value={crm.reached} />
              <Line sub label='Confirmed by the client' value={crm.confirmed} />
              <Line sub label='From chats with an agent' value={crm.from_agent_chats} />
              <Line sub label='Sent without a YES (the client left)' value={crm.defaults} />
              <Line
                label='Closed by an agent, not sent'
                value={crm.closed_by_agent}
                cls={crm.closed_by_agent ? 'text-k-b3261e' : undefined}
                action={crm.closed_by_agent ? leadsLink('not_in_crm', 'See them') : undefined}
              />
              <Line
                label='Refused before the CRM'
                value={crm.refused}
                cls={crm.refused ? 'text-k-8a4b00' : undefined}
                action={crm.refused ? leadsLink('not_in_crm', 'See them') : undefined}
              />
              {Object.entries(crm.refused_reasons).map(([code, n]) => (
                <Line key={code} sub label={gateReasonText(code).replace(/^./, (c) => c.toUpperCase())} value={n} />
              ))}
              <Line label='Not sent yet' value={crm.not_sent_yet} action={crm.not_sent_yet ? leadsLink('not_in_crm', 'See them') : undefined} />
            </ul>
            <p className='m-0 text-xs text-k-6b6b6b'>Chats started in this period, with a phone. Closing a chat doesn’t send it.</p>
          </Panel>
        </div>
        <Panel
          title='The AI'
          right={
            <span
              className={cn(
                'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-bold',
                slow ? 'bg-k-fdecea text-k-b3261e' : 'bg-k-e3f6ea text-k-0a6b35'
              )}
            >
              {slow ? <TriangleAlert className='size-3.5' /> : <Check className='size-3.5' />}
              {slow ? 'Slower than the line' : 'Working normally'}
            </span>
          }
          className='self-start'
        >
          <p className='m-0 text-[13px] text-k-555555'>
            The answer time has a line: over it, it turns red here and shows up in Needs attention.
          </p>
          <ul className='m-0 flex list-none flex-col p-0'>
            <Line label='Cost' value={money(ai.cost)} />
            <Line label='Cost per ready lead' value={money(ai.cost_per_ready)} />
            <Line
              label='Answers from a fixed template'
              value={ai.answers ? `${pct(ai.template_answers, ai.answers)}%` : '—'}
            />
            <Line
              label='Typical answer time'
              value={
                <span className='inline-flex items-center gap-2'>
                  {ai.latency_median_ms === null ? '—' : `${(ai.latency_median_ms / 1000).toFixed(1)} s`}
                  <span className={cn('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-bold', slow ? 'bg-k-fdecea text-k-b3261e' : 'bg-k-e3f6ea text-k-0a6b35')}>
                    {slow ? <TriangleAlert className='size-3' /> : <Check className='size-3' />} line {ai.latency_line_ms / 1000} s
                  </span>
                </span>
              }
            />
          </ul>
          <p className='m-0 text-xs leading-[1.5] text-k-6b6b6b'>
            {plural(ai.answers, 'answer')} in this period. Template answers are the summary, the call-back reply, the goodbye and the
            fallbacks — most on purpose, so they have no line. Typical answer time is the median, from the client’s message to the reply.
          </p>
        </Panel>
      </div>
    </div>
  )
}
