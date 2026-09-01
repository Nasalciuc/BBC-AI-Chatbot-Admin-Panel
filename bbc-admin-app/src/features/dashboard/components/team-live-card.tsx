/**
 * Team live — who's in the queues RIGHT NOW.
 * Data source: GET /api/admin/agents/live, polled every 30s.
 * Kills the "am I even in the queue?" emails to the owner: every role sees
 * 🟢 ready · ⚪ online-not-ready · gray offline with last-seen age.
 *
 * Live members lead; the offline majority stays collapsed behind a toggle
 * and the list scrolls inside a capped height — the roster must never
 * swallow the dashboard.
 */

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronRight } from 'lucide-react'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { getLiveAgents } from '@/lib/api'
import { usePanelModeStore } from '@/stores/panel-mode-store'
import type { LiveAgent } from '@/lib/types'
import { describeLiveAgent, groupByRole, partitionLive } from './live-state'

const ROLE_LABELS: Record<string, string> = {
  sales: 'Sales',
  support: 'Support',
  supervisor: 'Supervisors',
  other: 'Other',
}

function AgentRow({ agent, now }: { agent: LiveAgent; now: Date }) {
  const d = describeLiveAgent(agent, now)
  return (
    <li className='flex items-center gap-2 text-sm'>
      <span className={`inline-block h-2 w-2 rounded-full ${d.dot}`} />
      <span className={d.state === 'offline' ? 'text-muted-foreground' : ''}>
        {agent.name ?? '—'}
      </span>
      {d.detail && (
        <span className='text-xs text-muted-foreground'>· {d.detail}</span>
      )}
    </li>
  )
}

export function TeamLiveCard() {
  const dormant = usePanelModeStore((s) => s.dormant)
  const { data: agents = [], isLoading } = useQuery<LiveAgent[]>({
    queryKey: ['agents-live'],
    queryFn: () => getLiveAgents(),
    refetchInterval: dormant ? false : 30_000,
  })
  const [showOffline, setShowOffline] = useState(false)

  const now = new Date()
  const { live, offline } = partitionLive(agents)
  const readyCount = live.filter((a) => a.is_ready).length

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
        {live.length > 0 && (
          <div className='max-h-64 space-y-3 overflow-y-auto'>
            {groupByRole(live).map(([role, members]) => (
              <div key={role}>
                <p className='mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground'>
                  {ROLE_LABELS[role] ?? role}
                </p>
                <ul className='space-y-1'>
                  {members.map((agent) => (
                    <AgentRow key={agent.id} agent={agent} now={now} />
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
        {!isLoading && live.length === 0 && (
          <p className='text-sm text-muted-foreground'>
            Nobody is online right now.
          </p>
        )}

        {offline.length > 0 && (
          <div>
            <button
              type='button'
              onClick={() => setShowOffline((v) => !v)}
              className='flex min-h-11 w-full items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground md:min-h-6'
            >
              {showOffline ? (
                <ChevronDown className='h-3.5 w-3.5' />
              ) : (
                <ChevronRight className='h-3.5 w-3.5' />
              )}
              {showOffline ? 'Hide' : 'Show'} {offline.length} offline
            </button>
            {showOffline && (
              <div className='mt-2 max-h-48 space-y-3 overflow-y-auto'>
                {groupByRole(offline).map(([role, members]) => (
                  <div key={role}>
                    <p className='mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground'>
                      {ROLE_LABELS[role] ?? role}
                    </p>
                    <ul className='space-y-1'>
                      {members.map((agent) => (
                        <AgentRow key={agent.id} agent={agent} now={now} />
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
