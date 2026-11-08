import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { claimConversation, getConversations, ApiError } from '@/lib/api'
import { type Conversation } from '@/lib/types'
import { cn } from '@/lib/utils'
import { usePanelModeStore } from '@/stores/panel-mode-store'
import { useQueueStore } from '@/stores/queue-store'
import { ChatAvatar } from './chat-avatar'
import { initials, minutesLabel, minutesSince, presenceView, routeText } from '../view'

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
 */
export function QueueSection({
  onClaimed,
  onOpen,
  canTake = true,
  rail = false,
  selectedId = null,
  flat = false,
}: {
  onClaimed: (id: string) => void
  /** Read the chat before taking it. */
  onOpen?: (id: string) => void
  /** An agent who is not ready sees the line but can't take from it. */
  canTake?: boolean
  /** The "wider chat" rail: avatars only. */
  rail?: boolean
  selectedId?: string | null
  /** On a white page (phones, the CRM dock) the card is grey, not white. */
  flat?: boolean
}) {
  const queueIds = useQueueStore((s) => s.queueIds)
  const queueCount = useQueueStore((s) => s.queueCount)
  const queryClient = useQueryClient()
  const dormant = usePanelModeStore((s) => s.dormant)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [nowMs, setNowMs] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNowMs(Date.now()), 15_000)
    return () => clearInterval(t)
  }, [])

  const { data: rows = [] } = useQuery({
    queryKey: ['queue-rows', queueIds.join(',')],
    enabled: queueIds.length > 0,
    refetchInterval: dormant ? false : 5_000,
    queryFn: async () => {
      const res = await getConversations({ assigned_to: 'none', status: 'active', limit: '50' })
      const wanted = new Set(queueIds)
      return (res.data ?? []).filter((c: Conversation) => wanted.has(c.id))
    },
  })

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
        // object (it only unwraps string details) — parse the winner out of it.
        let winner = ''
        try {
          const body = JSON.parse(err.message) as { detail?: { winner?: string } }
          if (body?.detail?.winner) winner = body.detail.winner
        } catch {
          /* not JSON — fall through to the neutral message */
        }
        // Neutral, not punitive — losing a race is sporting.
        toast.info(winner ? `${winner} took ${conv.visitor_name ? `${conv.visitor_name.split(' ')[0]}’s` : 'this'} chat a moment before you` : 'This one is already taken')
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
          return (
            <button
              key={conv.id}
              type='button'
              onClick={() => onOpen?.(conv.id)}
              aria-label={`${conv.visitor_name || 'Visitor'}, waiting ${minutesLabel(w)}`}
              title={`${conv.visitor_name || 'Visitor'} · waiting ${minutesLabel(w)}`}
              className='flex flex-col items-center gap-1'
            >
              <ChatAvatar
                text={initials(conv.visitor_name)}
                tone='bg-k-fff3e0 text-k-8a4b00'
                dot={p.dot}
                ring='border-k-f7f7f7'
                size={44}
                className={cn(selectedId === conv.id && 'shadow-[0_0_0_2px_var(--k-f7f7f7),0_0_0_4px_var(--kl-202020)]')}
              />
              <span aria-hidden='true' className={cn('text-[11px] font-bold', w > 5 ? 'text-k-b3261e' : 'text-k-8a4b00')}>
                {minutesLabel(w)}
              </span>
            </button>
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
        const route = routeText(conv)
        return (
          <div key={conv.id} className='flex items-center gap-2.5'>
            <ChatAvatar text={initials(conv.visitor_name)} tone='bg-k-fff3e0 text-k-8a4b00' dot={p.dot} />
            <button
              type='button'
              onClick={() => onOpen?.(conv.id)}
              className='flex min-w-0 flex-1 flex-col gap-[3px] text-start text-k-202020'
            >
              <span className='flex min-w-0 items-center gap-1.5'>
                <strong className='truncate text-sm'>{conv.visitor_name || 'Visitor'}</strong>
                <span className={cn('flex-none rounded-full px-[7px] py-px text-xs font-bold', w > 5 ? 'bg-k-fdecea text-k-b3261e' : 'bg-k-fff3e0 text-k-8a4b00')}>
                  {minutesLabel(w)}
                </span>
              </span>
              <span className='truncate text-xs text-k-555555'>
                {route || (conv.status === 'needs_agent' ? 'Asked for a person' : `${conv.message_count} messages`)}
              </span>
            </button>
            {canTake && (
              <button
                type='button'
                onClick={() => claim(conv)}
                disabled={busyId === conv.id}
                className='h-9 flex-none rounded-full bg-kb-202020 px-4 text-[13px] font-bold text-on-btn disabled:opacity-50'
              >
                {busyId === conv.id ? 'Taking…' : 'Take'}
              </button>
            )}
          </div>
        )
      })}
    </div>
  )
}
