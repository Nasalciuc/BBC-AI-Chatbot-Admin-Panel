/**
 * Honest client-presence display: state + age instead of stale binary truth.
 *
 * Red must be EARNED (an old "left" as the newest signal), never assumed:
 * page navigation fires pagehide/left and the next page's /open arrives
 * seconds later — a fresh "left" rendered red would be the panel lying.
 */

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
  if (!lastEventAt) return null
  const then = new Date(lastEventAt).getTime()
  if (Number.isNaN(then)) return null
  const mins = Math.floor((now.getTime() - then) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const h = Math.floor(mins / 60)
  const rem = mins % 60
  return rem > 0 ? `${h}h ${rem}m ago` : `${h}h ago`
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
      return { label: 'Active now', dot: 'bg-emerald-500', text: 'text-emerald-600', tone: 'green' }
    }
    return { ...NEUTRAL, label: age ? `Active · ${age}` : 'Active · a while ago' }
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

/** Presence key from conversation metadata, tolerating legacy shapes. */
export function presenceFromMetadata(m: Record<string, unknown>): string | undefined {
  const presence = m.widget_presence
  if (typeof presence === 'string' && presence) return presence
  if (m.widget_open === true || m.widget_open === 'true') return 'online'
  const reason = m.widget_last_close_reason
  if (typeof reason === 'string' && reason) return reason
  return undefined
}
