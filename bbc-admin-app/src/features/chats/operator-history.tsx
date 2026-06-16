import { useQuery } from '@tanstack/react-query'
import { getOperatorHistory } from '@/lib/api'
import { User, MessageSquare, AlertTriangle, Bot, Lock, Clock } from 'lucide-react'

interface OpEvent {
  action: string
  agent_name?: string
  happened_at: string
  handoff_reason?: string
  response_seconds?: number
}

const fmt = (iso: string) => {
  try {
    return new Date(iso).toLocaleTimeString('en-US', { hour12: false })
  } catch {
    return iso
  }
}

const ICONS: Record<string, { Icon: typeof User; color: string; label: string }> = {
  assigned: { Icon: User, color: 'text-blue-500', label: 'Assigned' },
  first_response: { Icon: MessageSquare, color: 'text-green-600', label: 'Responded' },
  deadline_fired: { Icon: AlertTriangle, color: 'text-red-500', label: 'Timeout' },
  closed: { Icon: Lock, color: 'text-gray-400', label: 'Closed' },
  connecting: { Icon: Clock, color: 'text-yellow-600', label: 'Connecting' },
  agent_joined: { Icon: User, color: 'text-green-600', label: 'Joined' },
}

export function OperatorHistory({ conversationId }: { conversationId: string }) {
  const { data } = useQuery({
    queryKey: ['op-history', conversationId],
    queryFn: () => getOperatorHistory(conversationId),
    staleTime: 30_000,
  })

  if (!data?.events?.length) {
    return (
      <div className="text-xs text-gray-500 flex items-center gap-1.5">
        <Bot className="w-3.5 h-3.5" />
        <span>AI handled entire conversation</span>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <h4 className="text-xs font-semibold text-[#C9A54E] uppercase tracking-wide">
        Operator History
      </h4>
      <div className="space-y-1.5">
        {data.events.map((ev: OpEvent, i: number) => {
          const cfg = ICONS[ev.action] ?? {
            Icon: Clock,
            color: 'text-gray-400',
            label: ev.action,
          }
          return (
            <div key={i} className="flex items-start gap-2 text-xs">
              <cfg.Icon className={`w-3.5 h-3.5 mt-0.5 shrink-0 ${cfg.color}`} />
              <div>
                <span className="text-gray-500">{cfg.label}</span>
                {ev.agent_name && (
                  <span className="text-gray-800 font-medium ml-1">{ev.agent_name}</span>
                )}
                <span className="text-gray-400 ml-1.5">{fmt(ev.happened_at)}</span>
                {ev.response_seconds != null && (
                  <span className="text-green-600 ml-1">({ev.response_seconds}s)</span>
                )}
                {ev.handoff_reason && (
                  <span className="text-gray-400 ml-1">({ev.handoff_reason})</span>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export function OperatorBadge({ conversationId }: { conversationId: string }) {
  const { data } = useQuery({
    queryKey: ['op-history', conversationId],
    queryFn: () => getOperatorHistory(conversationId),
    staleTime: 30_000,
  })
  if (!data?.summary) return null
  return <span className="text-xs text-gray-400 ml-2">{data.summary}</span>
}
