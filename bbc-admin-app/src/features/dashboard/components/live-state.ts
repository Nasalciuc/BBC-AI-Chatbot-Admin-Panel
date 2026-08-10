/**
 * Pure display logic for the Team live card — unit-testable without a DOM.
 *
 * 🟢 ready (online + is_ready) · ⚪ online, not ready · gray offline with age.
 */
import type { LiveAgent } from '@/lib/types'
// Relative import (not @/): this module is imported by the zero-dependency
// node test runner, which resolves no path aliases.
import { formatAge } from '../../../lib/format-age.ts'

export type LiveState = 'ready' | 'online' | 'offline'

export interface LiveDisplay {
  state: LiveState
  /** Tailwind class for the status dot. */
  dot: string
  /** Secondary text, e.g. "last seen 12m ago" — null when live. */
  detail: string | null
}

export function lastSeenLabel(lastSeen: string | null, now: Date): string | null {
  // Shared formatter caps magnitudes (m → h → d → mo): "last seen 2mo ago",
  // never "last seen 1447h 30m ago".
  const age = formatAge(lastSeen, now)
  return age ? `last seen ${age}` : null
}

/** Live members first, offline collapsed behind a toggle — a 53-row flat
 *  roster of offline seed users must never dominate the dashboard. */
export function partitionLive(agents: LiveAgent[]): {
  live: LiveAgent[]
  offline: LiveAgent[]
} {
  const live: LiveAgent[] = []
  const offline: LiveAgent[] = []
  for (const a of agents) (a.is_online ? live : offline).push(a)
  return { live, offline }
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
