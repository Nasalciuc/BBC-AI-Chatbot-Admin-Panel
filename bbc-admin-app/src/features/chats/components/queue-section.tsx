import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Users } from 'lucide-react'
import { claimConversation, getConversations, ApiError } from '@/lib/api'
import { type Conversation } from '@/lib/types'
import { useQueueStore } from '@/stores/queue-store'
import { Badge } from '@/components/ui/badge'

/**
 * The shared line — conversations waiting for ANY operator; first click wins.
 *
 * The ids come from the heartbeat (same source as the badge, so the two can
 * never disagree); the row data comes from the unassigned-conversations list
 * and is intersected with those ids. No timer is shown on the rows — a
 * countdown would turn the work into a reflex game.
 *
 * On 409 the row belongs to somebody else: a neutral toast names the winner,
 * the row leaves at the next heartbeat, nothing the operator typed is touched
 * and nothing opens by itself — the human decides what to take next.
 *
 * There is no "give it back" button (owner's decision): a client who just
 * reached a human must not be thrown back into the line, and in a system
 * built on competition, giving back what you took defeats the point.
 */
export function QueueSection({ onClaimed }: { onClaimed: (id: string) => void }) {
  const queueIds = useQueueStore((s) => s.queueIds)
  const queueCount = useQueueStore((s) => s.queueCount)
  const queryClient = useQueryClient()
  const [busyId, setBusyId] = useState<string | null>(null)

  const { data: rows = [] } = useQuery({
    queryKey: ['queue-rows', queueIds.join(',')],
    enabled: queueIds.length > 0,
    refetchInterval: 5_000,
    queryFn: async () => {
      const res = await getConversations({ assigned_to: 'none', status: 'active', limit: '50' })
      const wanted = new Set(queueIds)
      return (res.data ?? []).filter((c: Conversation) => wanted.has(c.id))
    },
  })

  if (queueCount === 0) return null

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
        toast.info(winner ? `${winner} took this conversation` : 'This one is already taken')
        queryClient.invalidateQueries({ queryKey: ['queue-rows'] })
      } else {
        toast.error('Could not take it — try again')
      }
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className='border-b border-border bg-amber-50/40 dark:bg-amber-900/10'>
      <div className='flex items-center gap-2 px-4 pt-3 pb-1'>
        <Users className='h-4 w-4 text-[#C9A54E]' />
        <span className='text-xs font-semibold text-foreground'>
          Waiting: {queueCount}
        </span>
      </div>
      {rows.map((conv) => (
        <div key={conv.id} className='flex items-center justify-between gap-2 px-4 py-2'>
          <div className='min-w-0 flex-1'>
            <div className='flex items-center gap-2'>
              {conv.chat_number != null && (
                <span className='shrink-0 font-mono text-[11px] text-muted-foreground'>
                  #{conv.chat_number}
                </span>
              )}
              <span className='truncate text-sm font-medium text-foreground'>
                {conv.visitor_name || 'Visitor'}
              </span>
              {conv.status === 'needs_agent' && (
                <Badge variant='outline' className='border-red-300 text-[11px] text-red-600'>
                  asked for an agent
                </Badge>
              )}
            </div>
            <div className='text-[11px] text-muted-foreground'>
              {conv.message_count != null ? `${conv.message_count} messages` : ''}
              {conv.updated_at
                ? ` · last activity ${new Date(conv.updated_at).toLocaleTimeString()}`
                : ''}
            </div>
          </div>
          <button
            onClick={() => claim(conv)}
            disabled={busyId === conv.id}
            className='shrink-0 rounded-md bg-[#0B1829] px-3 py-1.5 text-xs font-medium text-white hover:bg-[#0B1829]/90 disabled:opacity-50'
          >
            Take
          </button>
        </div>
      ))}
    </div>
  )
}
