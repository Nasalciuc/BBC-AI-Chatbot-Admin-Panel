/**
 * Honest client-presence display: state + age instead of stale binary truth.
 *
 * Red must be EARNED (an old "left" as the newest signal), never assumed:
 * page navigation fires pagehide/left and the next page's /open arrives
 * seconds later — a fresh "left" rendered red would be the panel lying.
 */

// Relative import (not @/): this module is imported by the zero-dependency
// node test runner, which resolves no path aliases.
import { formatAge } from '../../lib/format-age.ts'

export interface PresenceDisplay {
  label: string
  /** Tailwind class for the status dot. */
  dot: string
  /** Tailwind class for the label text. */
  text: string
  tone: 'green' | 'red' | 'neutral'
}

const FRESH_MS = 2 * 60 * 1000

function ageLabel(lastEventAt: string | undefined, now: Date): string | null {
  // Shared formatter: m → h → d → mo caps ("3d ago", "2mo ago" — never "1447h").
  return formatAge(lastEventAt, now)
}

function isFresh(lastEventAt: string | undefined, now: Date): boolean {
  if (!lastEventAt) return false
  const then = new Date(lastEventAt).getTime()
  if (Number.isNaN(then)) return false
  return now.getTime() - then < FRESH_MS
}

const NEUTRAL = { dot: 'bg-gray-400', text: 'text-gray-500', tone: 'neutral' as const }

export function describeClientPresence(
  presence: string | undefined,
  lastEventAt: string | undefined,
  now: Date,
): PresenceDisplay {
  const age = ageLabel(lastEventAt, now)
  const fresh = isFresh(lastEventAt, now)

  if (presence === 'online') {
    if (fresh) {
      // The age is shown even when live: an operator calibrates trust from
      // "last seen 12s ago", not from a green dot that might be an hour old.
      return {
        label: age ? `Active now · last seen ${age}` : 'Active now',
        dot: 'bg-emerald-500', text: 'text-emerald-600', tone: 'green',
      }
    }
    return { ...NEUTRAL, label: age ? `Active · ${age}` : 'Active · a while ago' }
  }

  // Derived server-side (widget pings every 30s while really open): two
  // missed pings and the stored "online" is no longer evidence of anyone.
  if (presence === 'stale') {
    return { ...NEUTRAL, label: age ? `No signal for ${age}` : 'No signal' }
  }

  if (presence === 'minimized') {
    const suffix = !fresh && age ? ` · ${age}` : ''
    return { ...NEUTRAL, label: `On site, chat closed${suffix}` }
  }

  if (presence === 'left') {
    if (fresh) {
      // Grace window — likely mid-navigation, not gone.
      return { ...NEUTRAL, label: 'Left just now' }
    }
    return {
      label: age ? `Left · ${age}` : 'Left',
      dot: 'bg-red-500',
      text: 'text-red-600',
      tone: 'red',
    }
  }

  return { ...NEUTRAL, label: 'Unknown' }
}

/**
 * Status dot for a conversation LIST row — the same aged truth the detail
 * header shows. The list rows were painting a fresh green dot on clients
 * idle for days: green must be EARNED by recent activity, never implied by
 * "status=active". List payloads carry no widget_presence, so recency of
 * `updated_at` stands in for the activity signal.
 */
export function listRowDot(
  status: string | undefined,
  lastActivityAt: string | undefined,
  now: Date,
): string {
  if (status === 'needs_agent') return 'bg-red-400'
  if (status === 'pending') return 'bg-yellow-400'
  if (status !== 'active') return 'bg-muted-foreground/50'
  return describeClientPresence('online', lastActivityAt, now).dot
}

/** Presence key from conversation metadata, tolerating legacy shapes.
 *
 * The server derives `widget_presence_effective` on read (age-aware) and
 * it WINS: a stored "online" is a claim the client may have abandoned
 * without ever managing to send a close beacon. */
export function presenceFromMetadata(m: Record<string, unknown>): string | undefined {
  const effective = m.widget_presence_effective
  if (typeof effective === 'string' && effective) return effective
  const presence = m.widget_presence
  if (typeof presence === 'string' && presence) return presence
  if (m.widget_open === true || m.widget_open === 'true') return 'online'
  const reason = m.widget_last_close_reason
  if (typeof reason === 'string' && reason) return reason
  return undefined
}
