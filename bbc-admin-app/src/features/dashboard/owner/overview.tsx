import { Link } from '@tanstack/react-router'
import type { OwnerDashboard } from '@/lib/types'
import { AttentionList, Headline, Panel, StatCard, linkCls } from './bits'
import { DayBars } from './charts'
import { ATTENTION_LINES, attentionView, delta, overviewHeadline, pct, plural, secondsText, type AttentionView } from './view'

export type Go = (to: AttentionView['action']['to'] | 'conversion') => void

/** A supervisor's or PM's cards: what their agents did with the chats. No
 *  Leads link (the page isn't theirs) and no AI-only rate (the owner's). */
function TeamCards({ d, go, tabs }: { d: OwnerDashboard; go: Go; tabs: boolean }) {
  const now = d.overview.now
  const a = d.agents.now
  return (
    <div className='grid grid-cols-1 gap-3.5 sm:grid-cols-2 xl:grid-cols-4'>
      <StatCard
        label='Ready to quote'
        value={String(now.ready)}
        delta={delta(now.ready, d.overview.before.ready, d.days)}
        tint='bg-k-fdf6e3'
      >
        {now.ready === 0
          ? 'No client confirmed a complete trip in this period.'
          : `${now.ready_ai_only} from chats only with the AI, ${now.ready_with_agent} with an agent: after the take nothing new is read from the chat.`}
      </StatCard>
      <StatCard label='Given to agents' value={String(a.given)}>
        Taken from the queue with Take, or given by the routing.
      </StatCard>
      <StatCard
        label='Went back to the assistant'
        value={String(a.went_back)}
        delta={delta(a.went_back, d.agents.before.went_back, d.days, { upIsGood: false })}
        action={
          tabs && a.went_back > 0 ? (
            <button type='button' onClick={() => go('agents')} className={linkCls}>
              See who
            </button>
          ) : undefined
        }
      >
        The agent didn’t answer in time.
      </StatCard>
      <StatCard
        label='First reply'
        value={secondsText(a.first_reply_median_s)}
        action={
          tabs ? (
            <button type='button' onClick={() => go('agent-table')} className={linkCls}>
              See by agent
            </button>
          ) : undefined
        }
      >
        Median, from the take to the first message. After 90 s the chat goes back to the assistant.
      </StatCard>
    </div>
  )
}

/** `tabs`: the other tabs can open (not on a phone, which shows only the Overview). */
export function OverviewCards({ d, go, tabs }: { d: OwnerDashboard; go: Go; tabs: boolean }) {
  const now = d.overview.now
  const before = d.overview.before
  const aiRate = pct(now.ai_only_ready, now.ai_only)
  const aiRateBefore = pct(before.ai_only_ready, before.ai_only)
  const back = d.agents.now.went_back
  return (
    <div className='grid grid-cols-1 gap-3.5 sm:grid-cols-2 xl:grid-cols-4'>
      <StatCard
        label='Ready to quote'
        value={String(now.ready)}
        delta={delta(now.ready, before.ready, d.days)}
        tint='bg-k-fdf6e3'
        action={
          <Link to='/leads' search={{ view: 'all', level: 'ready' } as never} className={linkCls}>
            See them in Leads
          </Link>
        }
      >
        {now.ready === 0
          ? 'No client confirmed a complete trip in this period.'
          : `${now.ready_ai_only} from chats only with the AI, ${now.ready_with_agent} with an agent: after the take nothing new is read from the chat.`}
      </StatCard>
      <StatCard
        label='AI chat → Ready'
        value={aiRate === null ? '—' : `${aiRate}%`}
        delta={aiRate !== null && aiRateBefore !== null ? delta(aiRate, aiRateBefore, d.days, { unit: ' pts' }) : null}
        action={
          tabs && (
            <button type='button' onClick={() => go('conversion')} className={linkCls}>
              See the steps
            </button>
          )
        }
      >
        {now.ai_only
          ? `${now.ai_only_ready} of ${plural(now.ai_only, 'chat')} only with the AI. Chats with an agent aren’t scored after the take.`
          : 'No chat only with the AI in this period.'}
      </StatCard>
      <StatCard
        label='Contact only'
        value={String(now.contact_only)}
        action={
          <Link to='/leads' search={{ view: 'all', level: 'contact' } as never} className={linkCls}>
            See them in Leads
          </Link>
        }
      >
        {now.started
          ? `${pct(now.contact_only, now.started)}% of ${plural(now.started, 'chat')}: the form, no message. Sent to the CRM with defaults.`
          : 'No chats in this period.'}
      </StatCard>
      <StatCard
        label='Went back to the assistant'
        value={String(back)}
        delta={delta(back, d.agents.before.went_back, d.days, { upIsGood: false })}
        action={
          tabs && (
            <button type='button' onClick={() => go('agents')} className={linkCls}>
              See Agents
            </button>
          )
        }
      >
        {d.agents.now.given ? `Of ${plural(d.agents.now.given, 'chat')} given to agents.` : 'No chat was given to an agent.'}
      </StatCard>
    </div>
  )
}

export function Overview({ d, go, tabs, today, team = false }: { d: OwnerDashboard; go: Go; tabs: boolean; today: string; team?: boolean }) {
  const items = d.attention.map((a) => attentionView(a, d.quality.qa_accounts))
  return (
    <div className='flex flex-col gap-[18px]'>
      <Headline text={overviewHeadline(d)} />
      {team ? <TeamCards d={d} go={go} tabs={tabs} /> : <OverviewCards d={d} go={go} tabs={tabs} />}
      <Panel title='Needs attention'>
        <AttentionList
          items={items}
          onAction={go}
          canOpen={(to) => tabs || to === 'review'}
          note={
            team
              ? 'Shown only past a line: a first reply over 60 s with a third of an agent’s chats back to the assistant (3 chats or more), an hour with 3 or more chats back.'
              : ATTENTION_LINES
          }
        />
      </Panel>
      <Panel
        title='Chats by day'
        hint={
          <>
            {d.days === 1 && 'The last 7 days. '}
            <span className='hidden md:inline'>Hover a day for its numbers.</span>
          </>
        }
      >
        <DayBars bars={d.overview.by_day} today={today} />
      </Panel>
    </div>
  )
}
