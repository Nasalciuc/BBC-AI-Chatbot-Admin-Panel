// Lightweight user shape used by the Teams UI (name resolution, pickers, counts).
export interface TeamUser {
  id: string
  name?: string | null
  email?: string | null
  role?: string | null
  team_id?: string | null
}

/** Best-effort readable message from a thrown API error (parses {detail}). */
export function apiErrorMessage(err: unknown, fallback = 'Something went wrong'): string {
  if (err instanceof Error) {
    const msg = err.message || fallback
    try {
      const parsed = JSON.parse(msg)
      if (parsed && typeof parsed === 'object' && 'detail' in parsed) {
        return String((parsed as { detail: unknown }).detail)
      }
    } catch {
      /* not JSON — use raw message */
    }
    return msg
  }
  return fallback
}

/** Format an HH:MM:SS / HH:MM time string to HH:MM; null-safe. */
export function fmtTime(t?: string | null): string | null {
  if (!t) return null
  return t.slice(0, 5)
}

export function fmtShift(
  shiftName?: string | null,
  start?: string | null,
  end?: string | null,
): string {
  const s = fmtTime(start)
  const e = fmtTime(end)
  const range = s && e ? `${s}–${e}` : s ? `${s}–…` : e ? `…–${e}` : ''
  if (shiftName && range) return `${shiftName} (${range})`
  return shiftName || range || '—'
}
