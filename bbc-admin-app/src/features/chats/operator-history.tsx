import { useQuery } from '@tanstack/react-query'
import { getOperatorHistory } from '@/lib/api'
import { useAuthStore } from '@/stores/auth-store'
import { User, MessageSquare, AlertTriangle, Bot, Lock, Clock } from 'lucide-react'

const HISTORY_ROLES = ['owner', 'admin', 'dev', 'supervisor', 'qa'] as const

function useCanViewHistory(): boolean {
  const roleRaw = useAuthStore((s) => s.auth.user?.role)
  const role = Array.isArray(roleRaw) ? roleRaw[0] : (roleRaw ?? '')
  return (HISTORY_ROLES as readonly string[]).includes(role)
}

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
  closed: { Icon: Lock, color: 'text-muted-foreground', label: 'Closed' },
  connecting: { Icon: Clock, color: 'text-yellow-600', label: 'Connecting' },
  agent_joined: { Icon: User, color: 'text-green-600', label: 'Joined' },
}

export function OperatorHistory({ conversationId }: { conversationId: string }) {
  const canViewHistory = useCanViewHistory()
  const { data } = useQuery<{ source: string; events: OpEvent[]; summary: string }>({
    queryKey: ['op-history', conversationId],
    queryFn: () => getOperatorHistory(conversationId),
    staleTime: 30_000,
    enabled: canViewHistory,
  })

  if (!canViewHistory) return null

  if (!data?.events?.length) {
    return (
      <div className="text-xs text-muted-foreground flex items-center gap-1.5">
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
            color: 'text-muted-foreground',
            label: ev.action,
          }
          return (
            <div key={i} className="flex items-start gap-2 text-xs">
              <cfg.Icon className={`w-3.5 h-3.5 mt-0.5 shrink-0 ${cfg.color}`} />
              <div>
                <span className="text-muted-foreground">{cfg.label}</span>
                {ev.agent_name && (
                  <span className="text-foreground font-medium ml-1">{ev.agent_name}</span>
                )}
                <span className="text-muted-foreground ml-1.5">{fmt(ev.happened_at)}</span>
                {ev.response_seconds != null && (
                  <span className="text-green-600 ml-1">({ev.response_seconds}s)</span>
                )}
                {ev.handoff_reason && (
                  <span className="text-muted-foreground ml-1">({ev.handoff_reason})</span>
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
  const canViewHistory = useCanViewHistory()
  const { data } = useQuery<{ source: string; events: OpEvent[]; summary: string }>({
    queryKey: ['op-history', conversationId],
    queryFn: () => getOperatorHistory(conversationId),
    staleTime: 30_000,
    enabled: canViewHistory,
  })
  if (!canViewHistory || !data?.summary) return null
  return <span className="ml-2 max-w-[10rem] truncate text-xs text-muted-foreground">{data.summary}</span>
}
