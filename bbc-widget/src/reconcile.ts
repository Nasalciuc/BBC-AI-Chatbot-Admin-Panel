/**
 * Message reconciliation — pure, node-testable.
 *
 * The race this kills (vladimirtechtest handoff): the client's optimistic
 * `temp-*` bubble was removed by EVERY poll batch and re-added only if the
 * server happened to include it — but the poll asks `?after=lastMsgTime`,
 * and an agent-joined burst advances that cursor PAST the user message's
 * server timestamp, so it never arrives in any batch → the client's own
 * message vanished from the UI while alive in the DB.
 *
 * New contract:
 *  - a POST /api/chat 200 is the save receipt: the send path PROMOTES the
 *    temp to `sent-*` (promoteTemp) — never again removable by a poll;
 *  - the poll only expires `temp-*` older than TEMP_TTL_MS (a failed send
 *    already shows an error bubble; its leftover temp ages out);
 *  - when a batch later DOES carry the user row, the local `sent-*` twin
 *    is replaced by it (content match) — no duplicates, server id wins.
 */

export interface WireMessage {
  id: string
  role: string
  content: string
  created_at: string
}

/** How long an unconfirmed temp may live — past this, the send failed. */
export const TEMP_TTL_MS = 10_000

export function tempTimestamp(id: string): number {
  const n = Number(id.slice(id.indexOf('-') + 1))
  return Number.isFinite(n) ? n : 0
}

export function promoteTemp<M extends WireMessage>(
  prev: M[],
  tempId: string,
  nowMs: number
): M[] {
  return prev.map((m) =>
    m.id === tempId ? { ...m, id: `sent-${nowMs}` } : m
  )
}

export interface ReconcileResult<M extends WireMessage> {
  next: M[]
  changed: boolean
  sawAi: boolean
}

export function reconcileBatch<M extends WireMessage>(
  prev: M[],
  incoming: M[],
  nowMs: number
): ReconcileResult<M> {
  // 1. Expire only STALE temps — fresh ones are in-flight sends, owned by
  //    the send response, not by the poll.
  const kept = prev.filter(
    (m) =>
      !m.id.startsWith('temp-') || nowMs - tempTimestamp(m.id) < TEMP_TTL_MS
  )

  const ids = new Set(kept.map((m) => m.id))
  const fresh = incoming.filter((m) => !ids.has(m.id))

  // 2. A server user-row replaces its local twin (content match) — the
  //    only merge key available; ids differ by construction.
  const freshUserBodies = new Set(
    fresh.filter((m) => m.role === 'user').map((m) => m.content)
  )
  const deduped = freshUserBodies.size
    ? kept.filter(
        (m) =>
          !(
            m.role === 'user' &&
            (m.id.startsWith('sent-') || m.id.startsWith('temp-')) &&
            freshUserBodies.has(m.content)
          )
      )
    : kept

  if (fresh.length === 0) {
    return {
      next: deduped.length !== prev.length ? deduped : prev,
      changed: deduped.length !== prev.length,
      sawAi: false,
    }
  }
  return {
    next: [...deduped, ...fresh],
    changed: true,
    sawAi: fresh.some((m) => m.role === 'ai'),
  }
}
