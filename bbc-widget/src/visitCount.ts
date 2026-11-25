/**
 * Whether this browser was on the site before this visit, for "On the site now" (the owner, 25 Nov 2026: a client
 * who comes back answers Skye's card almost twice as often, 8.8 % against 4.8 % in 11–24 Nov). A visit is a run of
 * pages and beats with no gap of 30 minutes, the gap the visit registry closes a visit after. The count is this
 * browser's own, in localStorage, from the first page this widget saw: no one else's cookie is read, and a
 * browser that keeps nothing is never "returning".
 */

const KEY = 'bbc_visits'
export const VISIT_GAP_MS = 30 * 60_000

export interface VisitCount { n: number; last: number }

/** The count after a page or a beat at nowMs: the same visit while the last was under VISIT_GAP_MS ago, one more
 *  after it. Anything unreadable starts again at 1; a clock set back reads as this visit, never a new one. */
export function countAfter(raw: string | null, nowMs: number): VisitCount {
  let kept: unknown = null
  try { kept = raw ? JSON.parse(raw) : null } catch { kept = null }
  const k = (kept && typeof kept === 'object' ? kept : {}) as Record<string, unknown>
  const n = typeof k.n === 'number' && Number.isInteger(k.n) && k.n >= 1 && k.n < 100_000 ? k.n : 0
  const last = typeof k.last === 'number' && Number.isFinite(k.last) && k.last > 0 ? k.last : 0
  if (!n || !last) return { n: 1, last: nowMs }
  return { n: nowMs - last >= VISIT_GAP_MS ? n + 1 : n, last: Math.max(last, nowMs) }
}

let returning = false

/** A page (entry.tsx) or a beat (live.ts): counted and kept. */
export function noteVisit(nowMs: number = Date.now()): void {
  try {
    const c = countAfter(localStorage.getItem(KEY), nowMs)
    localStorage.setItem(KEY, JSON.stringify(c))
    returning = c.n >= 2
  } catch { /* storage blocked: a browser we can't tell apart is not returning */ }
}

/** The number of this browser's visit, as the last page or beat counted it (0 when it can't say): the visit
 *  registry keeps a tab's card for this visit only (visits.ts). */
export function visitNumber(): number {
  try {
    const kept = JSON.parse(localStorage.getItem(KEY) ?? 'null') as { n?: unknown } | null
    return kept && typeof kept.n === 'number' && Number.isInteger(kept.n) && kept.n >= 1 ? kept.n : 0
  } catch { return 0 }
}

/** This browser came before this visit. */
export function isReturning(): boolean {
  return returning
}

/** The browser's time zone ("Australia/Sydney"), as the beat carries it; undefined when it can't say. */
export function timeZone(): string | undefined {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined } catch { return undefined }
}
