import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { ArrowUpRight, Check } from 'lucide-react'
import { getAgentDay } from '@/lib/api'
import type { AgentDay } from '@/lib/types'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth-store'
import { usePanelModeStore } from '@/stores/panel-mode-store'
import { useReadyStore } from '@/stores/ready-store'
import { PageHeader } from '@/components/layout/page-header'
import { HeaderActions } from '@/components/header-actions'
import { clock, joinAnd, minutesLabel, seconds } from '@/features/chats/view'

type Range = 1 | 7 | 30
const RANGES: { days: Range; label: string }[] = [
  { days: 1, label: 'Today' },
  { days: 7, label: '7 days' },
  { days: 30, label: '30 days' },
]

function dayLabel(date: string, today: boolean): string {
  const d = new Date(`${date}T12:00:00`)
  const s = `${d.toLocaleDateString('en-GB', { weekday: 'short' })} ${d.getDate()} ${d.toLocaleDateString('en-GB', { month: 'short' })}`
  return today ? `${s} (today)` : s
}

function openLabel(first: string): string {
  return first ? `Open ${first}’s chat` : 'Open the chat'
}

function chatLink(id: string) {
  return { to: '/chats' as const, search: { highlight: id } }
}

/**
 * Your day — an agent's own numbers. Nothing here ranks anyone: only the
 * caller's chats, compared with their own past weeks.
 */
export function YourDay() {
  const user = useAuthStore((s) => s.auth.user)
  const dormant = usePanelModeStore((s) => s.dormant)
  const deadline = useReadyStore((s) => s.responseDeadlineSeconds)
  const [range, setRange] = useState<Range>(1)
  // "updated N min ago" moves on its own; the clock lives in state, not render.
  const [nowMs, setNowMs] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNowMs(Date.now()), 30_000)
    return () => clearInterval(t)
  }, [])

  const { data, isLoading, isError, refetch, dataUpdatedAt } = useQuery({
    queryKey: ['agent-day', range],
    queryFn: () => getAgentDay(range),
    refetchInterval: dormant ? false : 60_000,
  })

  const updated = dataUpdatedAt ? Math.max(0, Math.floor((nowMs - dataUpdatedAt) / 60_000)) : null
  const d0 = new Date(nowMs)
  const today = dayLabel(`${d0.getFullYear()}-${String(d0.getMonth() + 1).padStart(2, '0')}-${String(d0.getDate()).padStart(2, '0')}`, false)
  const scope = range === 1 ? `today, ${today}` : `last ${range} days`

  return (
    <main id='content' className='flex flex-col gap-[18px] px-4 pt-6 pb-9 md:px-7 md:pt-7'>
      <PageHeader
        title='Your day'
        menuButton={false}
        subtitle={
          <>
            {user?.name} · your chats only · {scope}
            {updated !== null && ` · updated ${updated < 1 ? 'just now' : `${minutesLabel(updated)} ago`}`}
          </>
        }
        actions={
          <div className='flex flex-wrap items-center gap-2.5'>
            <div role='tablist' aria-label='Period' className='flex gap-1 rounded-2xl bg-k-ececec p-1'>
              {RANGES.map((r) => (
                <button
                  key={r.days}
                  type='button'
                  role='tab'
                  aria-selected={range === r.days}
                  onClick={() => setRange(r.days)}
                  className={cn(
                    'h-9 rounded-xl px-4 text-sm focus-visible:outline-2 focus-visible:outline-k-6ea8ff',
                    range === r.days ? 'bg-k-ffffff font-bold text-k-202020 shadow-sm' : 'text-k-202020'
                  )}
                >
                  {r.label}
                </button>
              ))}
            </div>
            <HeaderActions className='ms-0' />
          </div>
        }
      />

      {isError ? (
        <div role='alert' className='flex flex-wrap items-center gap-3 rounded-2xl bg-k-fff3e0 px-4 py-3 text-sm text-k-8a4b00'>
          <span className='flex-1'>
            <strong>Couldn’t load your day.</strong> The numbers aren’t shown rather than shown wrong.
          </span>
          <button type='button' onClick={() => refetch()} className='h-9 rounded-full bg-kb-202020 px-4 text-[13px] font-bold text-on-btn'>
            Try again
          </button>
        </div>
      ) : isLoading || !data ? (
        <div className='grid grid-cols-1 gap-3.5 sm:grid-cols-2 xl:grid-cols-4' aria-busy='true'>
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className='h-44 animate-pulse rounded-[20px] bg-k-f7f7f7' />
          ))}
        </div>
      ) : (
        <DayBody data={data} range={range} deadline={deadline} />
      )}
    </main>
  )
}

function Card({ label, value, children, link }: { label: string; value: string; children: React.ReactNode; link?: React.ReactNode }) {
  return (
    <section aria-label={label} className='flex flex-col gap-2 rounded-[20px] bg-k-f7f7f7 p-[18px]'>
      <span className='text-[11px] font-bold tracking-[0.05em] text-k-555555 uppercase'>{label}</span>
      <span className='text-[32px] leading-none font-bold text-k-202020'>{value}</span>
      <div className='flex flex-1 flex-col gap-1.5 text-sm leading-[1.45] text-k-444444'>{children}</div>
      {link}
    </section>
  )
}

const linkCls = 'mt-1 text-sm font-bold text-k-5b45e0 hover:underline focus-visible:outline-2 focus-visible:outline-k-6ea8ff'

function DayBody({ data, range, deadline }: { data: AgentDay; range: Range; deadline: number }) {
  const t = data.totals
  const when = range === 1 ? 'Today' : `In ${range} days`
  const wentBack = data.went_back[0]
  const notIn = data.not_in_crm
  const names = notIn.map((n) => n.first_name || 'A client')
  const firstNotIn = notIn[0]

  const items: { kind: 'urgent' | 'todo' | 'good'; text: string; sub?: string; action?: React.ReactNode }[] = []
  if (range === 1) {
    for (const w of data.went_back.slice(0, 2)) {
      items.push({
        kind: 'urgent',
        text: `${w.first_name || 'A client'}’s chat went back to the assistant at ${clock(w.at)}, before you answered.`,
        sub: 'If they ask for a person again, they show up under Waiting.',
        action: (
          <Link {...chatLink(w.conversation_id)} className={linkCls}>
            Open it
          </Link>
        ),
      })
    }
  }
  if (notIn.length) {
    const both = notIn.every((n) => n.has_phone && n.has_email)
    items.push({
      kind: 'todo',
      text: `${joinAnd(names.slice(0, 3))}${names.length > 3 ? ` and ${names.length - 3} more` : ''} ${notIn.length === 1 ? 'isn’t' : 'aren’t'} in the CRM yet.${both ? (notIn.length === 1 ? ' They left a phone and an e-mail.' : ' All left a phone and an e-mail.') : ''}`,
      sub: 'Send them from the chat, or save the lead number if you added them in the CRM yourself.',
      action: firstNotIn ? (
        <Link
          {...chatLink(firstNotIn.conversation_id)}
          className='flex h-10 items-center rounded-full bg-kb-202020 px-4 text-sm font-bold whitespace-nowrap text-on-btn focus-visible:outline-2 focus-visible:outline-k-6ea8ff'
        >
          {openLabel(firstNotIn.first_name)}
        </Link>
      ) : undefined,
    })
  }
  for (const w of data.waiting_on_you.slice(0, 3)) {
    items.push({
      kind: 'todo',
      text: `${w.first_name || 'A client'} hasn’t had a reply from you for ${minutesLabel(w.minutes)}.`,
      sub:
        w.left_minutes_ago !== null
          ? `They left the site ${minutesLabel(w.left_minutes_ago)} ago. If they don’t come back, the chat closes by itself ${w.left_minutes_ago >= 5 ? 'any moment' : `in about ${Math.max(1, 5 - w.left_minutes_ago)} min`}.`
          : undefined,
      action: (
        <Link {...chatLink(w.conversation_id)} className={linkCls}>
          {openLabel(w.first_name)}
        </Link>
      ),
    })
  }
  const fr = data.first_reply_week
  if (fr.this !== null && fr.previous !== null && fr.this < fr.previous) {
    items.push({ kind: 'good', text: `Your first reply this week: ${seconds(fr.this)}. The week before: ${seconds(fr.previous)}.` })
  }

  const rows = range === 1 ? data.by_day.slice(0, 7) : data.by_day
  const tableTitle = range === 30 ? 'Last 30 days, day by day' : range === 7 ? 'Last 7 days, day by day' : 'This week, day by day'

  return (
    <>
      <div className='grid grid-cols-1 gap-3.5 sm:grid-cols-2 xl:grid-cols-4'>
        <Card
          label='Chats you took'
          value={String(t.took)}
          link={
            <Link to='/chats' className={linkCls}>
              Open Conversations
            </Link>
          }
        >
          <span>
            {when}. {t.still_open} {t.still_open === 1 ? 'is' : 'are'} still open.
          </span>
          {t.wait_median_s !== null && (
            <span className='text-[13px] text-k-555555'>Clients waited {seconds(t.wait_median_s)} before you took them (median).</span>
          )}
        </Card>
        <Card label='Your first reply' value={seconds(t.first_reply_median_s)}>
          <span>Median, from taking the chat to your first message.</span>
          <span className='text-[13px] text-k-555555'>
            After {deadline} seconds without a reply the chat goes back to the assistant.
          </span>
        </Card>
        <Card
          label='Went back to the assistant'
          value={String(t.went_back)}
          link={
            wentBack ? (
              <Link {...chatLink(wentBack.conversation_id)} className={linkCls}>
                {openLabel(wentBack.first_name)}
              </Link>
            ) : undefined
          }
        >
          <span>
            {wentBack
              ? `${wentBack.first_name || 'A client'}’s chat, at ${clock(wentBack.at)}. No reply within ${deadline} seconds of taking it.`
              : `None. Every chat you took got an answer within ${deadline} seconds.`}
          </span>
        </Card>
        <Card
          label='In the CRM from your chats'
          value={`${t.in_crm} of ${t.with_lead}`}
          link={
            firstNotIn ? (
              <Link {...chatLink(firstNotIn.conversation_id)} className={linkCls}>
                {openLabel(firstNotIn.first_name)}
              </Link>
            ) : undefined
          }
        >
          <span>
            {notIn.length
              ? `${joinAnd(names.slice(0, 2))}${names.length > 2 ? ` and ${names.length - 2} more` : ''} ${notIn.length === 1 ? 'isn’t' : 'aren’t'} in the CRM yet. Closing a chat doesn’t send it.`
              : t.with_lead
                ? 'All of them are in the CRM.'
                : 'No leads from your chats yet.'}
          </span>
        </Card>
      </div>

      <section aria-label='For you' className='flex flex-col rounded-[20px] bg-k-ffffff px-5 pt-[18px] pb-4 shadow-[0_0_0_1px_var(--k-ececec)]'>
        <h2 className='m-0 mb-1 text-lg font-bold text-k-202020'>For you</h2>
        {items.length === 0 ? (
          <p className='m-0 py-3 text-sm text-k-555555'>Nothing waits for you right now.</p>
        ) : (
          <ul className='m-0 list-none p-0'>
            {items.map((it, i) => (
              <li key={i} className='flex flex-wrap items-start gap-3 border-t border-k-f0f0f0 py-3.5'>
                <span
                  aria-hidden='true'
                  className={cn(
                    'flex size-[26px] flex-none items-center justify-center rounded-full text-sm font-bold',
                    it.kind === 'urgent' ? 'bg-k-fde8e6 text-k-d93025' : it.kind === 'todo' ? 'bg-k-efeaff text-k-4a36c4' : 'bg-k-e3f6ea text-k-0a6b35'
                  )}
                >
                  {it.kind === 'urgent' ? '!' : it.kind === 'todo' ? <ArrowUpRight className='size-3.5' /> : <Check className='size-3.5' />}
                </span>
                <div className='flex min-w-[220px] flex-1 flex-col gap-1'>
                  <p className='m-0 text-[15px] leading-[1.45] text-k-202020'>
                    <span
                      className={cn(
                        'me-2 text-[11px] font-bold tracking-[0.05em] uppercase',
                        it.kind === 'urgent' ? 'text-k-b3261e' : it.kind === 'todo' ? 'text-k-4a36c4' : 'text-k-0a6b35'
                      )}
                    >
                      {it.kind === 'urgent' ? 'Urgent' : it.kind === 'todo' ? 'To do' : 'Good'}
                    </span>
                    {it.text}
                  </p>
                  {it.sub && <p className='m-0 text-[13px] leading-[1.45] text-k-555555'>{it.sub}</p>}
                </div>
                {it.action}
              </li>
            ))}
          </ul>
        )}
        <p className='m-0 border-t border-k-f0f0f0 pt-3 text-xs text-k-555555'>Compared with your own past weeks, never with colleagues.</p>
      </section>

      <section aria-label={tableTitle} className='flex flex-col gap-2 rounded-[20px] bg-k-ffffff px-5 pt-[18px] pb-4 shadow-[0_0_0_1px_var(--k-ececec)]'>
        <h2 className='m-0 text-lg font-bold text-k-202020'>{tableTitle}</h2>
        <div className='overflow-x-auto'>
          <table className='w-full min-w-[640px] border-collapse text-sm'>
            <thead>
              <tr className='text-[13px] text-k-555555'>
                <th className='border-b border-k-ececec py-2.5 pe-3 text-start font-normal'>Day</th>
                <th className='border-b border-k-ececec px-3 py-2.5 text-end font-normal'>Chats you took</th>
                <th className='border-b border-k-ececec px-3 py-2.5 text-end font-normal'>First reply</th>
                <th className='border-b border-k-ececec px-3 py-2.5 text-end font-normal'>Went back to the assistant</th>
                <th className='border-b border-k-ececec px-3 py-2.5 text-end font-normal'>Closed</th>
                <th className='border-b border-k-ececec py-2.5 ps-3 text-end font-normal'>In the CRM</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.date} className='text-k-202020'>
                  <td className={cn('border-b border-k-f0f0f0 py-3 pe-3', i === 0 && 'font-bold')}>{dayLabel(r.date, i === 0)}</td>
                  <td className='border-b border-k-f0f0f0 px-3 py-3 text-end'>{r.took}</td>
                  <td className='border-b border-k-f0f0f0 px-3 py-3 text-end'>{r.took ? seconds(r.first_reply_median_s) : '—'}</td>
                  <td className={cn('border-b border-k-f0f0f0 px-3 py-3 text-end', r.went_back ? 'font-bold text-k-b3261e' : 'text-k-6b6b6b')}>{r.went_back}</td>
                  <td className='border-b border-k-f0f0f0 px-3 py-3 text-end'>{r.closed}</td>
                  <td className='border-b border-k-f0f0f0 py-3 ps-3 text-end font-bold'>{r.with_lead ? `${r.in_crm} of ${r.with_lead}` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className='m-0 text-xs leading-normal text-k-555555'>
          Only your own chats. Nobody else’s numbers are shown here, and there’s no ranking.
          <br />
          First reply: from taking the chat to your first message. Went back: no reply within {deadline} seconds of taking it. In the CRM: your chats whose client has a lead in the CRM, whoever sent it.
        </p>
      </section>
    </>
  )
}
