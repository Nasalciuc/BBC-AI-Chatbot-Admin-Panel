/**
 * Team live — who's in the queues RIGHT NOW.
 * Data source: GET /api/admin/agents/live, polled every 30s.
 * Kills the "am I even in the queue?" emails to the owner: every role sees
 * 🟢 ready · ⚪ online-not-ready · gray offline with last-seen age.
 */

import { useQuery } from '@tanstack/react-query'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { getLiveAgents } from '@/lib/api'
import type { LiveAgent } from '@/lib/types'
import { describeLiveAgent, groupByRole } from './live-state'

const ROLE_LABELS: Record<string, string> = {
  sales: 'Sales',
  support: 'Support',
  supervisor: 'Supervisors',
  other: 'Other',
}

export function TeamLiveCard() {
  const { data: agents = [], isLoading } = useQuery<LiveAgent[]>({
    queryKey: ['agents-live'],
    queryFn: () => getLiveAgents(),
    refetchInterval: 30_000,
  })

  const now = new Date()
  const readyCount = agents.filter((a) => a.is_online && a.is_ready).length

  return (
    <Card>
      <CardHeader className='pb-2'>
        <CardTitle className='text-base'>Team live</CardTitle>
        <CardDescription>
          {isLoading
            ? 'Loading…'
            : `${readyCount} ready of ${agents.length} in the roster`}
        </CardDescription>
      </CardHeader>
      <CardContent className='space-y-3'>
        {groupByRole(agents).map(([role, members]) => (
          <div key={role}>
            <p className='mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground'>
              {ROLE_LABELS[role] ?? role}
            </p>
            <ul className='space-y-1'>
              {members.map((agent) => {
                const d = describeLiveAgent(agent, now)
                return (
                  <li key={agent.id} className='flex items-center gap-2 text-sm'>
                    <span className={`inline-block h-2 w-2 rounded-full ${d.dot}`} />
                    <span className={d.state === 'offline' ? 'text-muted-foreground' : ''}>
                      {agent.name ?? '—'}
                    </span>
                    {d.detail && (
                      <span className='text-xs text-muted-foreground'>· {d.detail}</span>
                    )}
                  </li>
                )
              })}
            </ul>
          </div>
        ))}
        {!isLoading && agents.length === 0 && (
          <p className='text-sm text-muted-foreground'>No active operators.</p>
        )}
      </CardContent>
    </Card>
  )
}
