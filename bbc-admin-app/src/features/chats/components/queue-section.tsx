import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { claimConversation, getConversations, ApiError } from '@/lib/api'
import { type Conversation } from '@/lib/types'
import { cn } from '@/lib/utils'
import { usePanelModeStore } from '@/stores/panel-mode-store'
import { useQueueStore } from '@/stores/queue-store'
import { ChatAvatar } from './chat-avatar'
import { countdown, firstName, holdButton, holdLeft, holdLine, initials, minutesLabel, minutesSince, presenceView, queueLine, takeRefusal } from '../view'

/**
 * The shared line — conversations waiting for ANY operator; first click wins.
 *
 * The ids come from the heartbeat (same source as the badge, so the two can
 * never disagree); the row data comes from the unassigned-conversations list
 * and is intersected with those ids. Oldest first, with how long each client
 * has waited: the wait is the client's, not a countdown on the operator.
 *
 * On 409 the row belongs to somebody else: a neutral toast names the winner,
 * the row leaves at the next heartbeat, nothing the operator typed is touched
 * and nothing opens by itself — the human decides what to take next.
 *
 * There is no "give it back" button (owner's decision): a client who just
 * reached a human must not be thrown back into the line, and in a system
 * built on competition, giving back what you took defeats the point.
 *
 * The line is taken blind (owner's decision, Nov 2026): who, how long, and
 * whether they asked for a person — never where they fly, never their words.
 *
 * A chat whose client has left no contact yet is held for up to two minutes
 * from their first message while the AI gets a phone or an email (the server's
 * contact hold, 25 Nov 2026): its Take counts down, and the clock ticks every
 * second only while a row is held.
 * Only agents get this line (the heartbeat fills it for sales and support),
 * and the list endpoint no longer sends them the route or the last message
 * of a chat that isn't theirs. Nothing here opens the chat before Take.
 */
export function QueueSection({
  onClaimed,
  canTake = true,
  rail = false,
  flat = false,
}: {
  onClaimed: (id: string) => void
  /** An agent who is not ready sees the line but can't take from it. */
  canTake?: boolean
  /** The "wider chat" rail: avatars and Take only. */
  rail?: boolean
  /** On a white page (phones, the CRM dock) the card is grey, not white. */
  flat?: boolean
}) {
  const queueIds = useQueueStore((s) => s.queueIds)
  const queueCount = useQueueStore((s) => s.queueCount)
  const queryClient = useQueryClient()
  const dormant = usePanelModeStore((s) => s.dormant)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [nowMs, setNowMs] = useState(() => Date.now())
  const [ticking, setTicking] = useState(false)
  useEffect(() => {
    // Every second while a Take counts down (the contact hold), every 15 s for the waits.
    const t = setInterval(() => setNowMs(Date.now()), ticking ? 1_000 : 15_000)
    return () => clearInterval(t)
  }, [ticking])

  // No timer of its own (PR-10): the heartbeat carries the queue ids, and the
  // key changes with them, so the rows refetch exactly when the line changes.
  const { data: rows = [] } = useQuery({
    queryKey: ['queue-rows', queueIds.join(',')],
    enabled: queueIds.length > 0 && !dormant,
    staleTime: 30_000,
    queryFn: async () => {
      const res = await getConversations({ assigned_to: 'none', status: 'active', limit: '50' })
      const wanted = new Set(queueIds)
      return (res.data ?? []).filter((c: Conversation) => wanted.has(c.id))
    },
  })

  const anyHeld = rows.some((c: Conversation) => holdLeft(c, nowMs) > 0)
  useEffect(() => { setTicking(anyHeld) }, [anyHeld])

  if (queueCount === 0) return null

  const waited = (c: Conversation) => minutesSince(c.queued_at ?? c.updated_at, nowMs) ?? 0
  const ordered = [...rows].sort((a, b) => waited(b) - waited(a))

  const claim = async (conv: Conversation) => {
    setBusyId(conv.id)
    try {
      await claimConversation(conv.id)
      queryClient.invalidateQueries({ queryKey: ['conversations'] })
      queryClient.invalidateQueries({ queryKey: ['queue-rows'] })
      onClaimed(conv.id)
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // apiFetch keeps the raw JSON body as the message when `detail` is an
        // object (it only unwraps string details): takeRefusal reads it.
        const { winner, holdSeconds } = takeRefusal(err.message)
        if (holdSeconds) {
          // The AI is still getting the contact: a wait, not a lost race.
          toast.info(holdLine(firstName(conv.visitor_name), holdSeconds))
        } else {
          // Neutral, not punitive — losing a race is sporting.
          toast.info(winner ? `${winner} took ${conv.visitor_name ? `${conv.visitor_name.split(' ')[0]}’s` : 'this'} chat a moment before you` : 'This one is already taken')
        }
        queryClient.invalidateQueries({ queryKey: ['queue-rows'] })
      } else {
        toast.error('Could not take it — try again')
      }
    } finally {
      setBusyId(null)
    }
  }

  if (rail) {
    return (
      <div className='flex flex-col items-center gap-3'>
        <span className='text-[11px] font-bold tracking-[0.05em] text-k-6b6b6b uppercase'>Waiting</span>
        {ordered.map((conv) => {
          const w = waited(conv)
          const p = presenceView(conv.client_presence, conv.client_presence_age_seconds)
          const who = conv.visitor_name || 'Visitor'
          const held = holdLeft(conv, nowMs)
          return (
            <div key={conv.id} title={held > 0 ? holdLine(firstName(conv.visitor_name), held) : `${who} · waiting ${minutesLabel(w)}`} className='flex flex-col items-center gap-1'>
              <ChatAvatar text={initials(conv.visitor_name)} tone='bg-k-fff3e0 text-k-8a4b00' dot={p.dot} ring='border-k-f7f7f7' size={44} />
              <span className={cn('text-[11px] font-bold', w > 5 ? 'text-k-b3261e' : 'text-k-8a4b00')}>{minutesLabel(w)}</span>
              {canTake && (
                <button
                  type='button'
                  onClick={() => claim(conv)}
                  disabled={busyId === conv.id || held > 0}
                  aria-label={held > 0 ? holdLine(firstName(conv.visitor_name), held) : `Take ${who}’s chat`}
                  className='h-7 rounded-full bg-kb-202020 px-3 text-xs font-bold text-on-btn disabled:opacity-50'
                >
                  {busyId === conv.id ? '…' : held > 0 ? countdown(held) : 'Take'}
                </button>
              )}
            </div>
          )
        })}
        <span aria-hidden='true' className='h-px w-10 bg-k-e3e2e2' />
      </div>
    )
  }

  return (
    <div className={cn('flex flex-col gap-3 rounded-2xl p-3', flat ? 'bg-k-f7f7f7' : 'bg-k-ffffff shadow-[0_1px_2px_rgba(32,32,32,0.06)]')}>
      <div className='flex items-center gap-2'>
        <span className='flex-1 text-[11px] font-bold tracking-[0.05em] text-k-6b6b6b uppercase'>Waiting for a person</span>
        <span className='text-xs text-k-6b6b6b'>oldest first</span>
      </div>
      {ordered.length === 0 && <span className='text-[13px] text-k-555555'>{queueCount} waiting…</span>}
      {ordered.map((conv) => {
        const w = waited(conv)
        const p = presenceView(conv.client_presence, conv.client_presence_age_seconds)
        const held = holdLeft(conv, nowMs)
        return (
          <div key={conv.id} className='flex items-center gap-2.5'>
            <ChatAvatar text={initials(conv.visitor_name)} tone='bg-k-fff3e0 text-k-8a4b00' dot={p.dot} />
            <div className='flex min-w-0 flex-1 flex-col gap-[3px] text-k-202020'>
              <span className='flex min-w-0 items-center gap-1.5'>
                <strong className='truncate text-sm'>{conv.visitor_name || 'Visitor'}</strong>
                <span className={cn('flex-none rounded-full px-[7px] py-px text-xs font-bold', w > 5 ? 'bg-k-fdecea text-k-b3261e' : 'bg-k-fff3e0 text-k-8a4b00')}>
                  {minutesLabel(w)}
                </span>
              </span>
              <span className='truncate text-xs text-k-555555'>{held > 0 ? 'The AI is getting a phone or email' : queueLine(conv)}</span>
            </div>
            {canTake && (
              <button
                type='button'
                onClick={() => claim(conv)}
                disabled={busyId === conv.id || held > 0}
                title={held > 0 ? holdLine(firstName(conv.visitor_name), held) : undefined}
                className='h-9 flex-none rounded-full bg-kb-202020 px-4 text-[13px] font-bold text-on-btn disabled:opacity-50'
              >
                {busyId === conv.id ? 'Taking…' : held > 0 ? holdButton(held) : 'Take'}
              </button>
            )}
          </div>
        )
      })}
    </div>
  )
}
