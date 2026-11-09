import { useMemo, useState } from 'react'
import { Link } from '@tanstack/react-router'
import type { AgentRow, OwnerDashboard } from '@/lib/types'
import { cn } from '@/lib/utils'
import { PersonAvatar } from '@/components/person-avatar'
import { AttentionList, Headline, OpenChats, Panel, StatCard, linkCls } from './bits'
import { HourBars } from './charts'
import {
  agentsHeadline,
  attentionView,
  beforeWords,
  hourLong,
  overDeadlineText,
  plural,
  secondsText,
  sortAgents,
  zoneText,
  type AgentSortKey,
  type AttentionView,
} from './view'

const COLUMNS: { key: AgentSortKey; label: string; cls?: string }[] = [
  { key: 'ready_hours', label: 'Ready' },
  { key: 'chats', label: 'Chats' },
  { key: 'first_reply_median_s', label: 'First reply' },
  { key: 'went_back', label: 'Went back to the assistant' },
  { key: 'after_yes', label: 'After a YES' },
  { key: 'never_wrote', label: 'Never wrote' },
  { key: 'not_in_crm', label: 'Not in the CRM' },
]

function ReplyDelta({ now, before }: { now: number | null; before: number | null }) {
  if (now === null || before === null || now === before) return null
  const slower = now > before
  return (
    <span className={cn('ms-1.5 text-[11px] font-bold', slower ? 'text-k-b3261e' : 'text-k-0a6b35')}>
      {slower ? '▲' : '▼'}
      {Math.abs(now - before)} s
    </span>
  )
}

function ByAgent({ d, help, canSeeTeam }: { d: OwnerDashboard; help: Set<string>; canSeeTeam: boolean }) {
  const [sort, setSort] = useState<AgentSortKey>('chats')
  const rows = useMemo(() => sortAgents(d.agents.by_agent as (AgentRow & Record<AgentSortKey, number | null>)[], sort), [d.agents.by_agent, sort])
  return (
    <Panel
      id='by-agent'
      title='By agent'
      hint='Click a column to sort, worst first.'
      right={
        canSeeTeam ? (
          <Link to='/teams' className={linkCls}>
            Who is Ready right now: Team
          </Link>
        ) : undefined
      }
    >
      {rows.length === 0 ? (
        <p className='m-0 text-sm text-k-555555'>No agent took a chat or was Ready in this period.</p>
      ) : (
        <div className='overflow-x-auto'>
          <table className='w-full min-w-[820px] border-collapse'>
            <thead>
              <tr className='border-b border-k-ececec'>
                <th className='px-2.5 py-2.5 text-left text-[12px] font-bold text-k-555555'>Agent</th>
                {COLUMNS.map((c) => (
                  <th key={c.key} className='px-2.5 py-2.5 text-right' aria-sort={sort === c.key ? 'descending' : 'none'}>
                    <button
                      type='button'
                      onClick={() => setSort(c.key)}
                      className={cn(
                        'max-w-[110px] text-right text-[12px] leading-tight hover:underline focus-visible:outline-2 focus-visible:outline-k-6ea8ff',
                        sort === c.key ? 'font-bold text-k-202020' : 'font-bold text-k-555555'
                      )}
                    >
                      {c.label}
                      {sort === c.key ? ' ↓' : ''}
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((a) => {
                const slow = a.first_reply_median_s !== null && a.first_reply_median_s > 60
                return (
                  <tr key={a.user_id} className='border-b border-k-ececec last:border-b-0'>
                    <td className='px-2.5 py-2.5'>
                      <div className='flex items-center gap-2.5'>
                        <PersonAvatar name={a.name} url={a.avatar_url} size={30} />
                        <strong className='text-[15px] text-k-202020'>{a.name}</strong>
                        {help.has(a.user_id) && (
                          <span className='rounded-full bg-k-fdecea px-2 py-0.5 text-[11px] font-bold text-k-b3261e'>Needs help</span>
                        )}
                      </div>
                    </td>
                    <td className='px-2.5 py-2.5 text-right text-sm text-k-202020'>{a.ready_hours} h</td>
                    <td className='px-2.5 py-2.5 text-right text-sm text-k-202020'>{a.chats}</td>
                    <td className={cn('px-2.5 py-2.5 text-right text-sm whitespace-nowrap', slow ? 'font-bold text-k-b3261e' : 'text-k-202020')}>
                      {secondsText(a.first_reply_median_s)}
                      <ReplyDelta now={a.first_reply_median_s} before={a.first_reply_before_s} />
                    </td>
                    <td className={cn('px-2.5 py-2.5 text-right text-sm', help.has(a.user_id) ? 'font-bold text-k-b3261e' : 'text-k-202020')}>{a.went_back}</td>
                    <td className={cn('px-2.5 py-2.5 text-right text-sm', a.after_yes ? 'text-k-c27a00' : 'text-k-6b6b6b')}>{a.after_yes}</td>
                    <td className={cn('px-2.5 py-2.5 text-right text-sm', a.never_wrote ? 'text-k-b3261e' : 'text-k-6b6b6b')}>{a.never_wrote}</td>
                    <td className='px-2.5 py-2.5 text-right text-sm font-bold text-k-202020'>
                      {a.not_in_crm > 0 && a.not_in_crm_conversations.length > 0 ? (
                        <OpenChats
                          chats={a.not_in_crm_conversations}
                          count={a.not_in_crm}
                          label={String(a.not_in_crm)}
                          teamOnly={d.scope?.team_scoped_links}
                        />
                      ) : (
                        a.not_in_crm
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className='m-0 text-[13px] leading-[1.5] text-k-555555'>
        Ready: hours with the status Ready. First reply: median, from the take to their first message. Never wrote: took the chat and
        sent nothing, and it didn’t go back to the assistant. Not in the CRM: their chats with a phone that never reached the CRM
        (closing doesn’t send it). Arrows: compared with {beforeWords(d.days)}.
      </p>
    </Panel>
  )
}

export function Agents({
  d,
  go,
  canSeeTeam = true,
}: {
  d: OwnerDashboard
  go: (to: AttentionView['action']['to']) => void
  /** The "Team" link: everyone but QA. */
  canSeeTeam?: boolean
}) {
  const a = d.agents.now
  // Already on Agents: the agent's line points at their row in the table.
  const items = d.attention
    .filter((x) => x.kind === 'agent' || x.kind === 'hour')
    .map((x) => attentionView(x, d.quality.qa_accounts))
    .map((v) => (v.action.to === 'agents' ? { ...v, action: { label: 'See the table', to: 'agent-table' as const } } : v))
  const help = new Set(d.attention.flatMap((x) => (x.kind === 'agent' ? [x.user_id] : [])))
  const worstRow = d.agents.by_hour.reduce((w, r) => (r.went_back > (w?.went_back ?? 0) ? r : w), null as null | (typeof d.agents.by_hour)[number])
  return (
    <div className='flex flex-col gap-[18px]'>
      <Headline text={agentsHeadline(d)} />
      <div className='grid grid-cols-1 gap-3.5 sm:grid-cols-2 xl:grid-cols-5'>
        <StatCard label='Given to agents' value={String(a.given)}>
          Taken from the queue with Take, or given by the routing.
        </StatCard>
        <StatCard label='First reply' value={secondsText(a.first_reply_median_s)}>
          Median, from the take to the first message. {overDeadlineText(a.replies_over_deadline, a.replies_measured)}
        </StatCard>
        <StatCard label='Went back to the assistant' value={String(a.went_back)}>
          The agent didn’t answer in time.
        </StatCard>
        <StatCard label='Came in after a YES' value={String(a.after_yes)}>
          The client had already confirmed the trip to the AI.
        </StatCard>
        <StatCard label='Agent never wrote' value={String(a.never_wrote)}>
          Taken or given, and nothing was sent.
        </StatCard>
      </div>
      <Panel title='Who needs help'>
        <AttentionList
          items={items}
          onAction={(to) =>
            to === 'hour' || to === 'agent-table'
              ? document.getElementById(to === 'hour' ? 'by-hour' : 'by-agent')?.scrollIntoView({ behavior: 'smooth' })
              : go(to)
          }
          note='The lines: a first reply over 60 s, at least 3 chats and a third of their chats back to the assistant; an hour with 3 or more chats back.'
          empty='Nobody is past the lines in this period.'
        />
      </Panel>
      <Panel id='by-hour' title='By hour' hint={`${zoneText()}. Chats given to agents, and how many went back to the assistant. Under each hour, how many people were Ready.`}>
        <HourBars rows={d.agents.by_hour} worst={worstRow && worstRow.went_back > 0 ? worstRow.hour : null} />
        {worstRow && worstRow.went_back > 0 && (
          <p className='m-0 text-[13px] text-k-555555'>
            Red: the hour with the most chats back to the assistant. {hourLong(worstRow.hour)}: {plural(worstRow.given, 'chat')} came in,{' '}
            {worstRow.went_back} went back, {plural(worstRow.ready_people, 'person', 'people')} {worstRow.ready_people === 1 ? 'was' : 'were'} Ready.
          </p>
        )}
      </Panel>
      <ByAgent d={d} help={help} canSeeTeam={canSeeTeam} />
    </div>
  )
}
