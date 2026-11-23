import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { Copy, Lock, Mail, MessageCircle, MessageSquareText, Phone, Send } from 'lucide-react'
import { toast } from 'sonner'
import { type ApiError, getOffers, markOffer } from '@/lib/api'
import type { OfferChannel, OfferDone, OfferMark, OfferRow } from '@/lib/types'
import { cn } from '@/lib/utils'
import { TONE_TEXT } from '@/features/leads/view'
import {
  MARK_RESTRICTED,
  QUEUE_NOTE,
  WINDOWS,
  WINDOW_LABEL,
  type OfferWindow,
  channelLine,
  chatsNote,
  clientName,
  crmNote,
  doneLine,
  emptyText,
  markedText,
  otherChats,
  refusedLine,
  summaryText,
  tripLine,
  waitingText,
  whoLine,
  windowOf,
} from '../offers'

const WINDOW_KEY = 'bbc_offers_window'

function readWindow(): OfferWindow {
  try {
    return windowOf(window.localStorage.getItem(WINDOW_KEY))
  } catch {
    return windowOf(null)
  }
}

const CHANNEL_ICON: Record<OfferChannel | 'any', { icon: React.ElementType; cls: string; label: string }> = {
  whatsapp: { icon: MessageCircle, cls: 'bg-k-e3f6ea text-k-0a6b35', label: 'WhatsApp' },
  text: { icon: MessageSquareText, cls: 'bg-k-e6f0fb text-k-1f5fb0', label: 'SMS' },
  call: { icon: Phone, cls: 'bg-k-e6f0fb text-k-1f5fb0', label: 'Call' },
  email: { icon: Mail, cls: 'bg-k-efeaff text-k-4a36c4', label: 'E-mail' },
  any: { icon: Send, cls: 'bg-k-f2f2f2 text-k-555555', label: 'Any channel' },
}

async function copyText(text: string, what: string) {
  try {
    await navigator.clipboard.writeText(text)
    toast.success(`${what} copied`)
  } catch {
    toast.error(`Couldn’t copy the ${what.toLowerCase()}.`)
  }
}

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

/** The client's contact, as the agent uses it: a link, and a copy button. */
function Contacts({ row }: { row: OfferRow }) {
  if (row.blind) return null
  return (
    <span className='flex flex-col gap-0.5 text-sm'>
      {row.phone && (
        <span className='flex min-h-8 items-center gap-2'>
          <a href={`tel:${row.phone}`} className='text-k-202020 hover:underline'>{row.phone}</a>
          <CopyButton text={row.phone} what='Phone' />
        </span>
      )}
      {row.phone_to_check && (
        <span className='flex min-h-8 flex-wrap items-center gap-2'>
          <span className='font-bold text-k-8a4b00'>Number to check: {row.phone_to_check}</span>
          <CopyButton text={row.phone_to_check} what='Number' />
          <span className='w-full text-xs text-k-6b6b6b'>Typed without a country code, and the country isn’t known: confirm it with the client before calling.</span>
        </span>
      )}
      {row.email && (
        <span className='flex min-h-8 items-center gap-2'>
          <a href={`mailto:${row.email}`} className='min-w-0 text-k-202020 [overflow-wrap:anywhere] hover:underline'>{row.email}</a>
          <CopyButton text={row.email} what='E-mail' />
        </span>
      )}
    </span>
  )
}

/** A mark this reader may not make: shown, disabled, with a lock and the reason (the panel's rule for
 *  restricted actions), never hidden — a reader who can't mark sees why (CodeRabbit #270). */
function LockedMark({ label, className }: { label: string; className: string }) {
  return (
    <button
      type='button'
      disabled
      title={MARK_RESTRICTED}
      aria-label={`${label} (${MARK_RESTRICTED.toLowerCase()})`}
      className={cn('inline-flex cursor-not-allowed items-center gap-1 opacity-60', className)}
    >
      <Lock aria-hidden='true' className='size-3' />
      {label}
    </button>
  )
}

function OfferItem({
  row,
  nowMs,
  canMark,
  busy,
  onMark,
  onOpenChat,
}: {
  row: OfferRow
  nowMs: number
  canMark: boolean
  busy: boolean
  onMark: (state: OfferMark) => void
  onOpenChat: () => void
}) {
  const channel = channelLine(row)
  const icon = CHANNEL_ICON[channel.kind]
  const Icon = icon.icon
  const refused = refusedLine(row.refused)
  const crm = crmNote(row)
  const chats = chatsNote(row)
  const name = clientName(row)
  return (
    <li className='flex flex-col gap-3 border-t border-k-ececec px-4 py-3.5 first:border-t-0 sm:flex-row sm:items-start sm:px-[18px]'>
      <div className='flex min-w-0 flex-1 items-start gap-3'>
        <span aria-hidden='true' title={icon.label} className={cn('flex size-8 flex-none items-center justify-center rounded-[10px]', icon.cls)}>
          <Icon className='size-4' />
        </span>
        <div className='flex min-w-0 flex-1 flex-col gap-1'>
          <span className='flex flex-wrap items-baseline gap-x-2 gap-y-0.5'>
            <strong className='text-[15px] leading-[1.35] text-k-202020'>{name}</strong>
            {row.client && row.chat_number != null && <span className='text-[13px] text-k-6b6b6b'>#{row.chat_number}</span>}
            {chats && <span className='rounded-md bg-k-f2f2f2 px-1.5 py-px text-xs font-bold text-k-555555'>{chats}</span>}
          </span>
          <span className='text-[13px] text-k-555555'>{tripLine(row)}</span>
          <Contacts row={row} />
          {!row.blind && (
            <span className='flex flex-wrap gap-1.5'>
              <span className={cn('rounded-md px-1.5 py-px text-xs font-bold', icon.cls)}>{channel.text}</span>
              {refused && <span className='rounded-md bg-k-fdecea px-1.5 py-px text-xs font-bold text-k-b3261e'>{refused}</span>}
              {row.price_seen && <span className='rounded-md bg-k-fff3e0 px-1.5 py-px text-xs font-bold text-k-8a4b00'>Saw {row.price_seen}</span>}
            </span>
          )}
          <span className='text-xs text-k-6b6b6b'>
            {whoLine(row)}
            {crm && (
              <>
                {' · '}
                <span className={TONE_TEXT[crm.tone]}>{crm.text}</span>
              </>
            )}
          </span>
        </div>
      </div>
      <div className='flex flex-none flex-wrap items-center gap-2 ps-11 sm:w-[220px] sm:flex-col sm:items-end sm:ps-0'>
        <span className='text-[13px] font-bold text-k-202020 sm:text-[15px]'>{waitingText(row.waiting_since, nowMs)}</span>
        <span className='flex flex-wrap items-center gap-1.5 sm:justify-end'>
          {canMark && !row.blind && (
            <>
              <button
                type='button'
                disabled={busy}
                onClick={() => onMark('sent')}
                className='h-9 rounded-full bg-kb-202020 px-3.5 text-[13px] font-bold text-on-btn disabled:opacity-50'
              >
                Offer sent
              </button>
              <button
                type='button'
                disabled={busy}
                onClick={() => onMark('not_needed')}
                className='h-9 rounded-full bg-k-f2f2f2 px-3.5 text-[13px] font-bold text-k-202020 hover:bg-k-ececec disabled:opacity-50'
              >
                Not needed
              </button>
            </>
          )}
          {!canMark && !row.blind && (
            <>
              <LockedMark label='Offer sent' className='h-9 rounded-full bg-k-f2f2f2 px-3.5 text-[13px] font-bold text-k-555555' />
              <LockedMark label='Not needed' className='h-9 rounded-full bg-k-f2f2f2 px-3.5 text-[13px] font-bold text-k-555555' />
            </>
          )}
          <button
            type='button'
            onClick={onOpenChat}
            aria-label={`Open the chat: ${name}`}
            title='Open the chat'
            className='flex size-9 items-center justify-center rounded-full text-k-444444 hover:bg-k-f2f2f2 focus-visible:outline-2 focus-visible:outline-k-6ea8ff'
          >
            <MessageCircle aria-hidden='true' className='size-[18px]' />
          </button>
        </span>
      </div>
    </li>
  )
}

function DoneItem({ row, nowMs, tz, canMark, busy, onUndo }: { row: OfferDone; nowMs: number; tz: string; canMark: boolean; busy: boolean; onUndo: () => void }) {
  return (
    <li className='flex items-center gap-3 border-t border-k-ececec px-4 py-3 text-sm sm:px-[18px]'>
      <span aria-hidden='true' className={cn('flex size-[22px] flex-none items-center justify-center rounded-full text-xs', row.state === 'sent' ? 'bg-k-0a6b35 text-k-ffffff' : 'bg-k-ececec text-k-555555')}>
        {row.state === 'sent' ? '✓' : '–'}
      </span>
      <span className='min-w-0 flex-1'>
        <span className='block truncate text-k-202020'>{[clientName(row), row.blind ? null : row.route].filter(Boolean).join(' · ')}</span>
        <span className='block text-xs text-k-6b6b6b'>{doneLine(row, nowMs, tz)}</span>
      </span>
      {canMark && !row.blind && (
        <button type='button' disabled={busy} onClick={onUndo} className='flex-none text-sm font-bold text-k-5b45e0 hover:underline disabled:opacity-50'>
          Undo
        </button>
      )}
      {!canMark && !row.blind && <LockedMark label='Undo' className='flex-none text-sm font-bold text-k-555555' />}
    </li>
  )
}

/**
 * Offers to send (ADR-6b, ADR-8b): the queue the Tasks page opens on. Every client who left a contact and a
 * trip waits here, oldest first, with the channel they asked for, until someone marks the written offer
 * sent — or not needed. Alex never quotes a fare: this offer is the only way the client hears one.
 */
export function OffersList({ tz }: { tz: string }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [days, setDays] = useState<OfferWindow>(readWindow)
  const [showDone, setShowDone] = useState(false)
  const [nowMs, setNowMs] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNowMs(Date.now()), 30_000)
    return () => clearInterval(t)
  }, [])

  const list = useQuery({
    queryKey: ['offers', days],
    queryFn: () => getOffers(days),
    select: (r) => r.data,
    refetchInterval: 60_000,
  })
  const data = list.data
  const canMark = !!data?.can.mark

  const mark = useMutation({
    mutationFn: ({ row, state }: { row: OfferRow | OfferDone; state: OfferMark }) =>
      markOffer(row.conversation_id, state, otherChats(row)),
    onSuccess: (_r, v) => {
      qc.invalidateQueries({ queryKey: ['offers'] })
      const text = markedText(v.state, clientName(v.row))
      if (v.state === 'open') toast.success(text)
      else toast.success(text, { action: { label: 'Undo', onClick: () => mark.mutate({ row: v.row, state: 'open' }) } })
    },
    onError: (e) => toast.error((e as ApiError)?.message ?? 'Couldn’t save the mark. Try again.'),
  })

  const pickDays = (d: OfferWindow) => {
    setDays(d)
    try {
      window.localStorage.setItem(WINDOW_KEY, String(d))
    } catch {
      /* the choice just isn't remembered */
    }
  }
  const openChat = (conversationId: string) => navigate({ to: '/chats', search: { highlight: conversationId } as never })

  return (
    <div className='flex flex-col gap-4'>
      <div className='flex flex-wrap items-center gap-2.5'>
        <p className='m-0 min-w-0 flex-1 basis-[320px] text-[13px] leading-[1.45] text-k-555555'>{QUEUE_NOTE}</p>
        {data && <p className='m-0 text-[13px] font-bold text-k-202020'>{summaryText(data.counts)}</p>}
        <label className='flex items-center gap-2 text-[13px] text-k-555555'>
          <span className='sr-only'>Which leads</span>
          <select
            value={days}
            onChange={(e) => pickDays(windowOf(e.target.value))}
            className='h-9 rounded-[10px] border border-k-e3e2e2 bg-k-ffffff px-2.5 text-sm text-k-202020'
          >
            {WINDOWS.map((w) => (
              <option key={w} value={w}>{WINDOW_LABEL[w]}</option>
            ))}
          </select>
        </label>
      </div>

      {list.isError ? (
        <div role='alert' className='flex flex-wrap items-center gap-3 rounded-2xl bg-k-fff3e0 px-4 py-3 text-sm text-k-8a4b00'>
          <span className='flex-1'>
            <strong>Couldn’t load the offers.</strong> The queue isn’t shown rather than shown empty.
          </span>
          <button type='button' onClick={() => list.refetch()} className='h-9 rounded-full bg-kb-202020 px-4 text-[13px] font-bold text-on-btn'>
            Try again
          </button>
        </div>
      ) : list.isLoading || !data ? (
        <div className='h-64 animate-pulse rounded-[20px] bg-k-f7f7f7' aria-busy='true' />
      ) : (
        <div className='flex flex-col overflow-hidden rounded-[20px] bg-k-ffffff ring-1 ring-k-ececec'>
          <section aria-labelledby='offers-waiting'>
            <h2 id='offers-waiting' className='m-0 flex items-center gap-2 px-4 pt-4 pb-2 text-[13px] font-bold tracking-[0.04em] text-k-202020 uppercase sm:px-[18px]'>
              Waiting for an offer
              <span className='rounded-full bg-k-f2f2f2 px-1.5 text-xs text-k-555555'>{data.waiting.length}</span>
            </h2>
            {data.waiting.length === 0 ? (
              <p className='m-0 px-5 py-10 text-center text-sm text-k-555555'>{emptyText(windowOf(data.days))}</p>
            ) : (
              <ul className='m-0 list-none p-0'>
                {data.waiting.map((r) => (
                  <OfferItem
                    key={r.conversation_id}
                    row={r}
                    nowMs={nowMs}
                    canMark={canMark}
                    busy={mark.isPending}
                    onMark={(state) => mark.mutate({ row: r, state })}
                    onOpenChat={() => openChat(r.conversation_id)}
                  />
                ))}
              </ul>
            )}
          </section>
          <section aria-label='Marked' className='border-t border-k-ececec'>
            <div className='flex items-center gap-2 px-4 py-3.5 sm:px-[18px]'>
              <h2 className='m-0 text-[13px] font-bold tracking-[0.04em] text-k-0a6b35 uppercase'>Sent or not needed</h2>
              <span className='rounded-full bg-k-e3f6ea px-1.5 text-xs font-bold text-k-0a6b35'>{data.done.length}</span>
              {data.done.length > 0 && (
                <button type='button' aria-expanded={showDone} onClick={() => setShowDone((v) => !v)} className='ms-auto text-sm font-bold text-k-5b45e0 hover:underline'>
                  {showDone ? 'Hide' : 'Show'}
                </button>
              )}
            </div>
            {showDone && (
              <ul className='m-0 list-none p-0'>
                {data.done.map((d) => (
                  <DoneItem key={d.conversation_id} row={d} nowMs={nowMs} tz={tz} canMark={canMark} busy={mark.isPending} onUndo={() => mark.mutate({ row: d, state: 'open' })} />
                ))}
              </ul>
            )}
          </section>
        </div>
      )}
    </div>
  )
}
