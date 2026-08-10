/**
 * Shared relative-time formatting with sane magnitude caps: m → h → d → mo.
 *
 * The panel was printing raw hours everywhere — "last seen 1447h 30m ago",
 * SLA "1518h 50m" — magnitudes no human parses. One formatter, used by the
 * Team live card, Hot Leads SLA, the chats list, and the presence labels.
 */

const MIN_PER_HOUR = 60
const MIN_PER_DAY = 24 * MIN_PER_HOUR
const DAYS_PER_MONTH_CAP = 60 // beyond two months, months are the only honest unit

/** Duration in minutes → "37m" / "1h 20m" / "2h" / "3d" / "2mo". */
export function formatDuration(mins: number): string {
  if (!Number.isFinite(mins) || mins < 0) return ''
  const m = Math.floor(mins)
  if (m < 1) return '0m'
  if (m < MIN_PER_HOUR) return `${m}m`
  if (m < MIN_PER_DAY) {
    const h = Math.floor(m / MIN_PER_HOUR)
    const rem = m % MIN_PER_HOUR
    return rem ? `${h}h ${rem}m` : `${h}h`
  }
  const days = Math.floor(m / MIN_PER_DAY)
  if (days < DAYS_PER_MONTH_CAP) return `${days}d`
  return `${Math.floor(days / 30)}mo`
}

/** ISO timestamp → "just now" / "10m ago" / "1h 20m ago" / "3d ago" / "2mo ago".
 *  Returns null for missing/unparseable input. */
export function formatAge(iso: string | null | undefined, now: Date): string | null {
  if (!iso) return null
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return null
  const mins = Math.floor((now.getTime() - then) / 60000)
  if (mins < 1) return 'just now'
  return `${formatDuration(mins)} ago`
}
