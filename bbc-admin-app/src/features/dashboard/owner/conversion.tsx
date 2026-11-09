import { useState } from 'react'
import type { OwnerDashboard, SourceTab } from '@/lib/types'
import { cn } from '@/lib/utils'
import { OpenChats, Panel, Pills, Td, Th } from './bits'
import { FUNNEL_STEPS, changesThisWeek, pct, plural, secondsText, sourceSentence } from './view'

const BAR_SHADES = ['bg-k-c9d9f2', 'bg-k-9dbde9', 'bg-k-6f9fe0', 'bg-k-4a87d9', 'bg-k-2a78d6']

function Meter({ value, max, cls }: { value: number; max: number; cls: string }) {
  return (
    <div aria-hidden='true' className='h-6 min-w-0 flex-1 overflow-hidden rounded-[6px] bg-k-f2f2f2'>
      <div className={cn('h-full rounded-[6px]', cls)} style={{ width: `${max ? (value / max) * 100 : 0}%` }} />
    </div>
  )
}

function ChangeThisWeek({ d }: { d: OwnerDashboard }) {
  const changes = changesThisWeek(d.leave, d.days)
  return (
    <section aria-label='What to change this week' className='flex flex-col gap-3 rounded-[20px] bg-k-f3f0ff p-5'>
      <h2 className='m-0 text-lg font-bold text-k-2c2266'>What to change this week</h2>
      {changes.length === 0 ? (
        <p className='m-0 text-sm text-k-2c2266'>Nothing to change: no single question loses a fifth of the people who leave, and nobody left after a promise.</p>
      ) : (
        <ol className='m-0 flex list-none flex-col p-0'>
          {changes.map((c, i) => (
            <li key={c.key} className='flex flex-wrap items-start gap-3 border-t border-k-efeaff py-3.5 first:border-t-0 first:pt-1 sm:flex-nowrap'>
              <span aria-hidden='true' className='flex size-6 flex-none items-center justify-center rounded-full bg-k-5b45e0 text-xs font-bold text-on-btn'>
                {i + 1}
              </span>
              <div className='flex min-w-0 flex-1 flex-col gap-1'>
                <strong className='text-[15px] text-k-202020'>{c.title}</strong>
                <span className='text-[13px] leading-[1.45] text-k-444444'>{c.text}</span>
              </div>
              <OpenChats chats={c.chats} count={c.count} label={`Open the ${plural(c.count, 'chat')}`} />
            </li>
          ))}
        </ol>
      )}
      <p className='m-0 text-xs text-k-4a36c4'>
        From fixed rules, not the AI: one question that loses a fifth of the people who leave, a promise people leave after. At most two at a time.
      </p>
    </section>
  )
}

function Funnel({ d }: { d: OwnerDashboard }) {
  const now = d.overview.now
  const ai = d.funnel.ai_only
  const ag = d.funnel.with_agent
  return (
    <Panel
      title='From chat to ready to quote'
      right={
        d.funnel.time_to_ready_median_s !== null ? (
          <span className='text-sm text-k-444444'>
            Time to ready, median: <strong>{secondsText(d.funnel.time_to_ready_median_s)}</strong> with the AI only
          </span>
        ) : undefined
      }
    >
      <div className='grid grid-cols-1 items-stretch gap-2 md:grid-cols-[200px_1fr]'>
        <div className='flex flex-col justify-center'>
          <strong className='text-[15px] text-k-202020'>All chats</strong>
          <span className='text-[13px] text-k-6b6b6b'>{plural(now.started, 'chat')} started</span>
        </div>
        <div className='flex min-w-0 gap-1 overflow-hidden rounded-xl'>
          {now.wrote > 0 && (
            <div className='flex min-w-[120px] flex-col justify-center bg-kb-202020 px-3.5 py-2 text-on-btn' style={{ flex: now.wrote }}>
              <strong className='text-lg leading-tight'>{now.wrote}</strong>
              <span className='text-[13px]'>Wrote at least one message</span>
            </div>
          )}
          {now.contact_only > 0 && (
            <div className='flex min-w-[120px] flex-col justify-center bg-k-fde3d6 px-3.5 py-2 text-k-7a2e0e' style={{ flex: now.contact_only }}>
              <strong className='text-lg leading-tight'>{now.contact_only}</strong>
              <span className='text-[13px]'>Contact only: left the form, never wrote</span>
            </div>
          )}
        </div>
      </div>
      <div className='overflow-x-auto'>
        <table className='w-full min-w-[640px] border-collapse'>
          <thead>
            <tr className='border-b border-k-ececec'>
              <th className='w-[200px] py-2 text-left align-bottom text-[12px] font-bold text-k-555555'>Of the chats where the client wrote</th>
              <th className='py-2 text-left align-bottom font-normal' colSpan={3}>
                <span className='me-1.5 rounded-full bg-k-efeaff px-1.5 py-0.5 text-[10.5px] font-bold text-k-4a36c4'>AI</span>
                <strong className='text-[15px] text-k-202020'>Only the AI</strong>
                <span className='block text-[13px] text-k-6b6b6b'>{plural(now.ai_only, 'chat')}, never given to an agent</span>
              </th>
              <th className='py-2 ps-4 text-left align-bottom font-normal' colSpan={3}>
                <span className='me-1.5 rounded-full bg-kb-202020 px-1.5 py-0.5 text-[10.5px] font-bold text-on-btn'>Agent</span>
                <strong className='text-[15px] text-k-202020'>With an agent</strong>
                <span className='block text-[13px] text-k-6b6b6b'>{plural(now.with_agent, 'chat')} · what the assistant had when the agent came in</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {FUNNEL_STEPS.map((label, i) => {
              const agentEmpty = i >= 3
              return (
                <tr key={label} className='border-b border-k-ececec'>
                  <td className={cn('py-2.5 pe-3 text-sm text-k-202020', i === 4 && 'font-bold')}>{label}</td>
                  <td className='w-[40%] py-2.5'>
                    <Meter value={ai[i]} max={now.ai_only} cls={BAR_SHADES[i]} />
                  </td>
                  <td className='w-12 py-2.5 text-right text-base font-bold text-k-202020'>{ai[i]}</td>
                  <td className='w-12 py-2.5 ps-2 text-right text-[13px] text-k-6b6b6b'>{pct(ai[i], now.ai_only) ?? '—'}%</td>
                  <td className='w-[30%] py-2.5 ps-4'>
                    <Meter value={agentEmpty ? 0 : ag[i]} max={now.with_agent} cls={BAR_SHADES[i]} />
                  </td>
                  <td className='w-12 py-2.5 text-right text-base font-bold text-k-202020'>{agentEmpty ? '—' : ag[i]}</td>
                  <td className='w-12 py-2.5 ps-2 text-right text-[13px] text-k-6b6b6b'>
                    {agentEmpty ? '' : `${pct(ag[i], now.with_agent) ?? '—'}%`}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className='m-0 flex flex-wrap items-start gap-2 text-[13px] leading-[1.5] text-k-555555'>
        <span className='rounded-full bg-k-fdf1d0 px-2 py-0.5 text-[11px] font-bold text-k-7a5300'>Ready to quote</span>
        <span className='min-w-0 flex-1'>
          Route, a date in the future, travelers and the return, confirmed by the client to the assistant. The contact passed the CRM check. With an agent the chat isn’t read after the take, and a client can’t confirm to an agent yet, so those two rows stay empty. Counted in the period the chat started.
        </span>
      </p>
    </Panel>
  )
}

function WhereTheyLeave({ d }: { d: OwnerDashboard }) {
  const total = d.leave.total
  const max = Math.max(1, ...d.leave.rows.map((r) => r.chats))
  return (
    <Panel title='Where people leave' hint={`${plural(total, 'chat')} only with the AI wrote but never confirmed. Share: of those ${total}.`}>
      {total === 0 ? (
        <p className='m-0 text-sm text-k-555555'>Nobody left a chat with the AI before confirming in this period.</p>
      ) : (
        <div className='overflow-x-auto'>
          <table className='w-full min-w-[460px] border-collapse'>
            <thead>
              <tr className='border-b border-k-ececec'>
                <Th className='px-1'>What the AI had just said</Th>
                <Th className='px-1' />
                <Th className='px-1 text-right'>Chats</Th>
                <Th className='px-1 text-right'>Share</Th>
                <Th className='px-1 text-right'>vs before</Th>
                <Th className='px-1' />
              </tr>
            </thead>
            <tbody>
              {d.leave.rows.map((r) => {
                const dd = r.chats - r.before
                return (
                  <tr key={r.key} className='border-b border-k-ececec last:border-b-0'>
                    <Td className='px-1'>{r.label}</Td>
                    <Td className='w-16 px-1'>
                      <div aria-hidden='true' className='mt-1.5 h-2 overflow-hidden rounded-full bg-k-f2f2f2'>
                        <div className='h-full rounded-full bg-k-8a8a8a' style={{ width: `${(r.chats / max) * 100}%` }} />
                      </div>
                    </Td>
                    <Td className='px-1 text-right font-bold'>{r.chats}</Td>
                    <Td className='px-1 text-right text-k-6b6b6b'>{pct(r.chats, total)}%</Td>
                    <Td className='px-1 text-right'>
                      {dd !== 0 && (
                        <span className={cn('rounded-full px-1.5 py-0.5 text-[11px] font-bold', dd > 0 ? 'bg-k-fdecea text-k-b3261e' : 'bg-k-e3f6ea text-k-0a6b35')}>
                          {dd > 0 ? '▲' : '▼'} {Math.abs(dd)}
                        </span>
                      )}
                    </Td>
                    <Td className='px-1 text-right'>
                      <OpenChats chats={r.conversations} count={r.chats} />
                    </Td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}

const SOURCE_TABS: { key: SourceTab; label: string }[] = [
  { key: 'source', label: 'Source' },
  { key: 'site', label: 'Site' },
  { key: 'device', label: 'Device' },
  { key: 'page', label: 'Page' },
  { key: 'route', label: 'Route' },
]

function WhereTheyComeFrom({ d }: { d: OwnerDashboard }) {
  const [tab, setTab] = useState<SourceTab>('source')
  const rows = d.sources[tab] ?? []
  const sentence = sourceSentence(rows, tab)
  const label = SOURCE_TABS.find((t) => t.key === tab)?.label ?? ''
  return (
    <Panel title='Where they come from' right={<Pills label='Group by' value={tab} options={SOURCE_TABS} onChange={setTab} />}>
      {sentence && <p className='m-0 rounded-2xl bg-k-f3f0ff px-3.5 py-2.5 text-sm text-k-2c2266'>{sentence}</p>}
      {rows.length === 0 ? (
        <p className='m-0 text-sm text-k-555555'>No chats in this period.</p>
      ) : (
        <div className='overflow-x-auto'>
          <table className='w-full min-w-[380px] border-collapse'>
            <thead>
              <tr className='border-b border-k-ececec'>
                <Th className='px-1'>{label}</Th>
                <Th className='px-1 text-right'>Chats</Th>
                <Th className='px-1 text-right'>Ready</Th>
                <Th className='px-1'>Ready, if they wrote</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const p = pct(r.ready, r.wrote)
                return (
                  <tr key={r.name} className='border-b border-k-ececec last:border-b-0'>
                    <Td className='max-w-[220px] truncate px-1' >
                      <span title={r.name}>{r.name}</span>
                      {r.names ? <span className='ms-1 text-xs text-k-6b6b6b'>({r.names})</span> : null}
                    </Td>
                    <Td className='px-1 text-right'>{r.chats}</Td>
                    <Td className='px-1 text-right font-bold'>{r.ready}</Td>
                    <Td className='px-1'>
                      <div className='flex items-center gap-2'>
                        <div aria-hidden='true' className='h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-k-f2f2f2'>
                          <div className='h-full rounded-full bg-k-2a78d6' style={{ width: `${p ?? 0}%` }} />
                        </div>
                        <span className='w-10 text-right text-[13px] text-k-6b6b6b'>{p === null ? '—' : `${p}%`}</span>
                      </div>
                    </Td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      {tab === 'source' && d.source_spellings.length > 0 && (
        <p className='m-0 text-xs text-k-6b6b6b'>
          Spellings that differ only in capitals or spaces are counted as one ({d.source_spellings.map((s) => `“${s}”`).join(', ')}).
        </p>
      )}
    </Panel>
  )
}

export function Conversion({ d }: { d: OwnerDashboard }) {
  return (
    <div className='flex flex-col gap-[18px]'>
      <ChangeThisWeek d={d} />
      <Funnel d={d} />
      <div className='grid grid-cols-1 gap-[18px] xl:grid-cols-2'>
        <WhereTheyLeave d={d} />
        <WhereTheyComeFrom d={d} />
      </div>
    </div>
  )
}
