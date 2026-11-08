import { useState } from 'react'
import { ChevronDown, Copy } from 'lucide-react'
import { toast } from 'sonner'
import type { Conversation, Lead, Message } from '@/lib/types'
import { cn } from '@/lib/utils'
import { OperatorHistory } from '../operator-history'
import {
  aboutOf,
  clock,
  type CrmView,
  firstName,
  confirmedOf,
  levelOf,
  missingForReady,
  joinAnd,
  noSummaryText,
  type Owner,
  routeLine,
  signalsOf,
  summaryStamp,
  tripNote,
  tripRows,
  tripText,
  whereRows,
  type Row,
} from '../view'

type FoldKey = 'ai' | 'trip' | 'about' | 'contact' | 'lead' | 'where' | 'chat' | 'history'

function Fold({
  id,
  title,
  open,
  onToggle,
  extra,
  children,
}: {
  id: FoldKey
  title: React.ReactNode
  open: boolean
  onToggle: (k: FoldKey) => void
  extra?: React.ReactNode
  children?: React.ReactNode
}) {
  return (
    <section className='flex flex-col gap-3 rounded-2xl bg-k-ffffff px-4 py-3.5 shadow-[0_1px_2px_rgba(32,32,32,0.05)]'>
      <h3 className='m-0'>
        <button
          type='button'
          aria-expanded={open}
          onClick={() => onToggle(id)}
          className='flex w-full items-center gap-2 text-start text-[11px] font-bold tracking-[0.05em] text-k-6b6b6b uppercase focus-visible:outline-2 focus-visible:outline-k-6ea8ff'
        >
          <span className='flex flex-1 items-center gap-1.5'>{title}</span>
          {extra}
          <ChevronDown aria-hidden='true' className={cn('size-3.5 flex-none transition-transform', open && 'rotate-180')} />
        </button>
      </h3>
      {children}
    </section>
  )
}

async function copyText(text: string, what: string) {
  try {
    await navigator.clipboard.writeText(text)
    toast.success(`${what} copied`)
  } catch {
    toast.error(`Couldn’t copy the ${what.toLowerCase()}.`)
  }
}

/** The Contact card's copy button, for anything an agent pastes elsewhere. */
function CopyButton({ text, what }: { text: string; what: string }) {
  return (
    <button
      type='button'
      aria-label={`Copy ${what.toLowerCase()}`}
      title={`Copy ${what.toLowerCase()}`}
      onClick={() => copyText(text, what)}
      className='flex size-8 flex-none items-center justify-center rounded-full bg-k-f7f7f7 text-k-202020 hover:bg-k-f2f2f2 focus-visible:outline-2 focus-visible:outline-k-6ea8ff'
    >
      <Copy aria-hidden='true' className='size-3.5' />
    </button>
  )
}

function Rows({ rows, wide }: { rows: Row[]; wide: boolean }) {
  return (
    <dl className='m-0 flex flex-col gap-2'>
      {rows.map((r, i) => (
        <div key={`${r.label}-${i}`} className={cn('flex gap-3 text-[13px] leading-[1.4]', r.copy && 'min-h-8 items-center')}>
          <dt className={cn('flex-none text-k-6b6b6b', wide ? 'w-[104px]' : 'w-[84px]')}>{r.label}</dt>
          <dd className={cn('m-0 min-w-0 flex-1 [overflow-wrap:anywhere]', r.warn ? 'font-bold text-k-8a4b00' : 'text-k-202020', r.strong && 'font-bold')}>
            {r.value}
          </dd>
          {r.copy && <CopyButton text={r.copy.text} what={r.copy.what} />}
        </div>
      ))}
    </dl>
  )
}

export interface DetailsPanelProps {
  conv: Conversation
  lead: Lead | null | undefined
  owner: Owner
  messages: Message[]
  isSupervisor: boolean
  showCost: boolean
  canViewHistory: boolean
  wide: boolean
  crm: CrmView
  canPush: boolean
  canManual: boolean
  pushing: boolean
  pushError: string | null
  onPush: () => void
  onSaveManual: (crmId: string) => Promise<boolean>
}

/** About this visitor: what the assistant understood, the trip, the person,
 *  the contact, the lead and the CRM, where they came from, and the chat. */
export function DetailsPanel(p: DetailsPanelProps) {
  const { conv, lead, owner } = p
  const [folds, setFolds] = useState<Partial<Record<FoldKey, boolean>>>({})
  const [sumAll, setSumAll] = useState(false)
  const [manualOpen, setManualOpen] = useState(false)
  const [manualId, setManualId] = useState('')
  const [savingManual, setSavingManual] = useState(false)
  const closedByDefault: FoldKey[] = ['about', 'where', 'history', ...(p.wide ? [] : (['chat'] as FoldKey[]))]
  const isOpen = (k: FoldKey) => (k in folds ? !!folds[k] : !closedByDefault.includes(k))
  const toggle = (k: FoldKey) => setFolds((f) => ({ ...f, [k]: !isOpen(k) }))

  const first = firstName(conv.visitor_name)
  const named = !!conv.visitor_name?.trim()
  const signals = signalsOf(lead)
  const trip = tripRows(lead, signals)
  const about = aboutOf(signals, lead)
  const level = levelOf(lead, confirmedOf(conv))
  const missing = conv.tunnel === 'support' ? [] : missingForReady(lead, confirmedOf(conv))
  const where = p.isSupervisor ? [] : whereRows(conv.metadata, conv.visitor_phone_country)
  const clientMsgs = p.messages.filter((m) => m.role === 'user').length
  const note = tripNote(conv, owner)
  const route = routeLine(lead)
  // route_display is written as "ORIG → DEST" (lead_service): shown only if
  // someone changed it to something else.
  const small = lead?.route_display && lead.route_display !== route ? lead.route_display : ''
  const tripTitle = conv.tunnel === 'support' ? 'Booking' : 'Trip'
  const handoff = p.messages.find((m) => m.role === 'system' && /asked for a person|person/i.test(m.content))
  const firstAgent = p.messages.find((m) => m.role === 'agent')
  const firstReplyMin =
    handoff && firstAgent && new Date(firstAgent.created_at) > new Date(handoff.created_at)
      ? Math.max(0, Math.round((new Date(firstAgent.created_at).getTime() - new Date(handoff.created_at).getTime()) / 60_000))
      : null
  const chatRows: Row[] = [
    { label: 'Chat', value: `${conv.chat_number != null ? `#${conv.chat_number} · ` : ''}${conv.tunnel === 'support' ? 'Support' : 'Sales'}` },
    { label: 'Started', value: clock(conv.created_at) || '—' },
    { label: 'Messages', value: String(p.messages.filter((m) => m.role !== 'system').length) },
    ...(handoff ? [{ label: 'Asked for a person', value: clock(handoff.created_at) }] : []),
    ...(firstReplyMin !== null ? [{ label: 'First reply', value: firstReplyMin < 1 ? 'in under a minute' : `in ${firstReplyMin} min` }] : []),
    ...(p.showCost ? [{ label: 'AI cost', value: `$${conv.ai_cost_total.toFixed(2)}` }] : []),
  ]

  const saveManual = async () => {
    const v = manualId.trim()
    if (!v || savingManual) return
    setSavingManual(true)
    const ok = await p.onSaveManual(v)
    setSavingManual(false)
    if (ok) {
      setManualOpen(false)
      setManualId('')
    }
  }

  return (
    <div className='flex flex-col gap-3 p-3.5'>
      <Fold
        id='ai'
        open={isOpen('ai')}
        onToggle={toggle}
        title={
          <>
            <span className='rounded-full bg-k-efeaff px-1.5 py-px text-[11px] tracking-[0.03em] text-k-4a36c4'>AI</span>
            What it understood
          </>
        }
      >
        {isOpen('ai') &&
          (conv.summary ? (
            <>
              <p className={cn('m-0 text-sm leading-normal text-k-444444', !sumAll && 'line-clamp-3')}>{conv.summary}</p>
              {conv.summary.length > 150 && (
                <button type='button' onClick={() => setSumAll((v) => !v)} className='self-start text-[13px] font-bold text-k-5b45e0'>
                  {sumAll ? 'Show less' : 'Show all'}
                </button>
              )}
              <p className='m-0 text-xs leading-[1.45] text-k-6b6b6b'>{summaryStamp(conv)}</p>
            </>
          ) : (
            <p className='m-0 text-[13px] leading-[1.45] text-k-555555'>{noSummaryText(conv, owner, clientMsgs)}</p>
          ))}
      </Fold>

      {(lead || (conv.tunnel === 'sales' && owner !== 'closed')) && (
        <Fold id='trip' open={isOpen('trip')} onToggle={toggle} title={tripTitle}>
          <div className='flex items-center gap-2'>
            <div className='flex min-w-0 flex-1 flex-col gap-0.5'>
              <div className='text-2xl leading-[1.15] font-bold tracking-[0.02em] text-k-202020'>{route || 'Not known yet'}</div>
              {small && <div className='text-[13px] text-k-555555'>{small}</div>}
            </div>
            {route && <CopyButton text={route} what='Route' />}
          </div>
          {isOpen('trip') && (
            <>
              {trip.length > 0 && <Rows rows={trip} wide={p.wide} />}
              {(route || trip.length > 0) && (
                <button
                  type='button'
                  onClick={() => copyText(tripText(route, trip), tripTitle)}
                  className='flex min-h-8 items-center gap-1.5 self-start text-[13px] font-bold text-k-5b45e0'
                >
                  <Copy aria-hidden='true' className='size-3.5' />
                  Copy the {tripTitle.toLowerCase()}
                </button>
              )}
              {note && <p className='m-0 text-xs leading-[1.45] text-k-6b6b6b'>{note}</p>}
            </>
          )}
        </Fold>
      )}

      {(about.persona || about.rows.length > 0) && (
        <Fold id='about' open={isOpen('about')} onToggle={toggle} title={named ? `About ${first}` : 'About the client'}>
          {!isOpen('about') && (
            <span className='text-[13px] leading-[1.45] text-k-444444'>
              {[about.persona?.label, ...about.rows.slice(0, 3).map((r) => r.value)].filter(Boolean).join(' · ')}
            </span>
          )}
          {isOpen('about') && (
            <>
              {about.persona && (
                <div className='flex flex-col gap-1'>
                  <span className='self-start rounded-full bg-k-f2f2f2 px-2.5 py-1 text-[13px] font-bold text-k-202020'>{about.persona.label}</span>
                  <span className='text-[13px] text-k-555555'>{about.persona.why}</span>
                </div>
              )}
              {about.rows.length > 0 && <Rows rows={about.rows} wide={p.wide} />}
            </>
          )}
        </Fold>
      )}

      <Fold id='contact' open={isOpen('contact')} onToggle={toggle} title='Contact'>
        {isOpen('contact') &&
          (p.isSupervisor ? (
            <>
              <div className='text-sm leading-[1.45] text-k-202020'>
                {[conv.visitor_phone, conv.visitor_email].filter(Boolean).join(' · ') || 'No phone or e-mail yet'}
              </div>
              <div className='text-xs leading-[1.45] text-k-6b6b6b'>Masked for supervisors. The agent on the chat and the CRM have the full contact.</div>
            </>
          ) : (
            <div className='flex flex-col gap-1.5'>
              <div className='text-sm font-bold text-k-202020'>{conv.visitor_name || 'No name yet'}</div>
              <div className='flex min-h-8 items-center gap-2 text-sm'>
                <span className={cn('flex-1', conv.visitor_phone ? 'text-k-202020' : 'text-k-6b6b6b')}>
                  {conv.visitor_phone ? <a href={`tel:${conv.visitor_phone}`} className='hover:underline'>{conv.visitor_phone}</a> : 'No phone yet'}
                </span>
                {conv.visitor_phone && <CopyButton text={conv.visitor_phone} what='Phone' />}
              </div>
              <div className='flex min-h-8 items-center gap-2 text-sm'>
                <span className={cn('min-w-0 flex-1 [overflow-wrap:anywhere]', conv.visitor_email ? 'text-k-202020' : 'text-k-6b6b6b')}>
                  {conv.visitor_email ? <a href={`mailto:${conv.visitor_email}`} className='hover:underline'>{conv.visitor_email}</a> : 'No e-mail yet'}
                </span>
                {conv.visitor_email && <CopyButton text={conv.visitor_email} what='E-mail' />}
              </div>
            </div>
          ))}
      </Fold>

      {lead && (
        <Fold
          id='lead'
          open={isOpen('lead')}
          onToggle={toggle}
          title='Lead & CRM'
          extra={
            <span className={cn('rounded-full px-2 py-0.5 text-xs tracking-normal normal-case', level.ready ? 'bg-k-fff3e0 text-k-8a4b00' : 'bg-k-f0f0f0 text-k-444444')}>
              {level.chip}
            </span>
          }
        >
          {isOpen('lead') && (
            <>
              <div className='flex flex-col gap-1.5'>
                <div className='flex flex-wrap items-baseline gap-1.5'>
                  <strong className='text-lg text-k-202020'>{level.name}</strong>
                  <span className='text-[13px] text-k-6b6b6b'>{level.why}</span>
                </div>
                <div role='img' aria-label={`Level: ${level.name}`} className='flex gap-1'>
                  {[1, 2, 3, 4].map((n) => (
                    <span
                      key={n}
                      className={cn('h-1.5 flex-1 rounded-full', n <= level.steps ? (level.ready ? 'bg-k-d9a400' : 'bg-k-202020') : 'bg-k-f0f0f0')}
                    />
                  ))}
                </div>
                {missing.length > 0 && <span className='text-[13px] leading-[1.4] font-bold text-k-8a4b00'>Missing for Ready: {joinAnd(missing)}.</span>}
              </div>
              <div className='h-px bg-k-f0f0f0' />
              <div className='flex flex-col gap-1.5'>
                <div className={cn('flex items-center gap-2 text-sm font-bold', p.crm.fg)}>
                  <span aria-hidden='true' className={cn('size-2 rounded-full', p.crm.dot)} />
                  {p.pushing ? 'Sending to the CRM…' : p.crm.title}
                </div>
                {p.crm.text && <p className='m-0 text-[13px] leading-[1.45] text-k-555555'>{p.crm.text}</p>}
                {p.pushError && (
                  <p role='alert' className='m-0 rounded-[10px] bg-k-fdecea px-2.5 py-1.5 text-xs text-k-b3261e'>
                    {p.pushError}
                  </p>
                )}
                {p.canPush && (
                  <button
                    type='button'
                    onClick={p.onPush}
                    disabled={p.pushing}
                    className='mt-1 h-10 rounded-full bg-kb-202020 text-sm font-bold text-on-btn disabled:opacity-50'
                  >
                    {p.pushing ? 'Sending…' : 'Send to CRM'}
                  </button>
                )}
                {p.canManual &&
                  (!manualOpen ? (
                    <button type='button' onClick={() => setManualOpen(true)} className='min-h-8 self-start py-1 text-[13px] font-bold text-k-5b45e0'>
                      I added {named ? first : 'them'} in the CRM
                    </button>
                  ) : (
                    <div className='flex flex-col gap-2 pt-1'>
                      <label className='flex flex-col gap-1 text-xs text-k-555555'>
                        Lead number in the CRM
                        <input
                          type='text'
                          inputMode='numeric'
                          value={manualId}
                          onChange={(e) => setManualId(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') saveManual()
                          }}
                          placeholder='e.g. 208131'
                          className='h-10 rounded-[10px] border border-k-c9c9c9 bg-k-ffffff px-3 text-sm text-k-202020'
                        />
                      </label>
                      <div className='flex gap-2'>
                        <button type='button' onClick={saveManual} disabled={!manualId.trim() || savingManual} className='h-9 rounded-full bg-kb-202020 px-4 text-[13px] font-bold text-on-btn disabled:opacity-50'>
                          {savingManual ? 'Saving…' : 'Save'}
                        </button>
                        <button type='button' onClick={() => setManualOpen(false)} className='h-9 rounded-full border border-k-c9c9c9 bg-k-ffffff px-4 text-[13px] font-bold text-k-202020'>
                          Cancel
                        </button>
                      </div>
                    </div>
                  ))}
              </div>
            </>
          )}
        </Fold>
      )}

      {where.length > 0 && (
        <Fold id='where' open={isOpen('where')} onToggle={toggle} title={`Where ${named ? first : 'they'} came from`}>
          {isOpen('where') && <Rows rows={where} wide={p.wide} />}
        </Fold>
      )}

      <Fold
        id='chat'
        open={isOpen('chat')}
        onToggle={toggle}
        title='This chat'
        extra={conv.chat_number != null ? <span className='font-normal tracking-normal normal-case'>#{conv.chat_number}</span> : undefined}
      >
        {isOpen('chat') && <Rows rows={chatRows} wide={p.wide} />}
      </Fold>

      {p.canViewHistory && (
        <Fold id='history' open={isOpen('history')} onToggle={toggle} title='Who handled it'>
          {isOpen('history') && <OperatorHistory conversationId={conv.id} />}
        </Fold>
      )}
    </div>
  )
}
