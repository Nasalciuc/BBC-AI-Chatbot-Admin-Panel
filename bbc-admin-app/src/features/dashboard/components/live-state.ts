/**
 * Pure display logic for the Team live card — unit-testable without a DOM.
 *
 * 🟢 ready (online + is_ready) · ⚪ online, not ready · gray offline with age.
 */
import type { LiveAgent } from '@/lib/types'

export type LiveState = 'ready' | 'online' | 'offline'

export interface LiveDisplay {
  state: LiveState
  /** Tailwind class for the status dot. */
  dot: string
  /** Secondary text, e.g. "last seen 12m ago" — null when live. */
  detail: string | null
}

export function lastSeenLabel(lastSeen: string | null, now: Date): string | null {
  if (!lastSeen) return null
  const then = new Date(lastSeen).getTime()
  if (Number.isNaN(then)) return null
  const mins = Math.floor((now.getTime() - then) / 60000)
  if (mins < 1) return 'last seen just now'
  if (mins < 60) return `last seen ${mins}m ago`
  const h = Math.floor(mins / 60)
  return `last seen ${h}h${mins % 60 ? ` ${mins % 60}m` : ''} ago`
}

export function describeLiveAgent(agent: LiveAgent, now: Date): LiveDisplay {
  if (agent.is_online && agent.is_ready) {
    return { state: 'ready', dot: 'bg-emerald-500', detail: null }
  }
  if (agent.is_online) {
    return { state: 'online', dot: 'bg-gray-300', detail: 'online, not ready' }
  }
  return {
    state: 'offline',
    dot: 'bg-gray-400/50',
    detail: lastSeenLabel(agent.last_seen, now) ?? 'offline',
  }
}

/** Stable role grouping for the card: operators first, supervisors last. */
export function groupByRole(agents: LiveAgent[]): [string, LiveAgent[]][] {
  const order = ['sales', 'support', 'supervisor']
  const groups = new Map<string, LiveAgent[]>()
  for (const a of agents) {
    const key = order.includes(a.role) ? a.role : 'other'
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key)!.push(a)
  }
  return [...order, 'other']
    .filter((r) => groups.has(r))
    .map((r) => [r, groups.get(r)!])
}
