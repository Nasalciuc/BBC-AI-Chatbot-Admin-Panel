// bbc-widget/src/beforeYouGo.ts — when the widget asks the server for the before-you-go (B1, ADR-6d), as the client
// leaves. Pure: ChatWindow.tsx reads the chat and calls these; the server (bbc-chatbot-api app/pipeline/card_buttons.py,
// before_you_go) decides the rest and says it once per conversation. No imports, so the node tests load it as it is.
//
// Lever 6 of "Mai multe chaturi și leaduri" (owner, 25 Nov 2026): on 17-24 Nov 54 clients wrote nothing after an
// agent's first message, and the before-you-go reached none of them (it stopped at any agent's word). It now goes out
// when the client leaves the chat or the page with the agent's newest message unanswered. A quiet page does not count
// there: a client waiting for the agent ("one moment, I'm checking the fares") looks the same. And lever 5's gap: the
// email field opened and left empty is the client leaving when the chat closes or the tab goes, not someone typing.

/** How the client leaves: the tab went out of view, the chat was closed, the page went quiet with the chat open. */
export type Leaving = 'hidden' | 'closed' | 'idle'

/** The channel field: not open, open with nothing typed, or something typed in it. */
export type FieldState = 'none' | 'empty' | 'typed'

/** The server's "not yet" answers: a later leave may ask again (the others are final, or a failure not retried).
 *  just_asked: a question for the contact under two minutes old; answered_agent: the client answered the agent;
 *  agent_wrote: a quiet page after an agent (or a server from before 25 Nov); never_wrote: the client's first message
 *  is not stored yet. */
export const NOT_YET: ReadonlySet<string> = new Set(['just_asked', 'answered_agent', 'agent_wrote', 'never_wrote'])

/** After a "not yet", a quiet page asks again no sooner than this; leaving the chat or the page asks at once. */
export const NOT_YET_WAIT_MS = 30_000

// ── The tab's mark for a conversation (sessionStorage, ChatWindow BEFORE_YOU_GO_KEY) ──────────────────────────
// ASKED: the server answered for good (said, or never will be). `pending:<ms>`: asked, no answer yet. A page that goes
// while its ask is out (a hidden tab that navigates: keepalive carries the request, never the answer back to a page
// that is gone) leaves the pending mark behind; once it is PENDING_STALE_MS old, the next page in the tab may ask
// again (CodeRabbit #282). The server says it once per conversation, so an ask whose answer was lost is asked safely.

export const ASKED = '1'
/** The server answers in about a second: a mark this old belongs to a page that went before its answer came. */
export const PENDING_STALE_MS = 15_000

export function pendingMark(now: number): string {
  return `pending:${now}`
}

/** Whether the tab's mark lets this page ask: none, or a pending one whose answer never came back. */
export function markAllowsAsk(mark: string | null, now: number): boolean {
  if (!mark) return true
  const m = /^pending:(\d+)$/.exec(mark)
  return !!m && now - Number(m[1]) >= PENDING_STALE_MS
}

/** The tab's mark once the server answered: none after a "not yet" (a later leave asks again), ASKED otherwise; a
 *  request that failed is not tried again (as before). */
export function markAfter(out: { show: boolean; why?: string } | null): string | null {
  return out && !out.show && out.why && NOT_YET.has(out.why) ? null : ASKED
}

interface Row { role: string; id?: string }

/** A message the server has. The client's send still on its way, or one that failed (`temp-*`, beside its error
 *  note in ChatWindow), is not one: a reply to the agent that never left answers nothing (CodeRabbit #282). */
function stored(m: Row): boolean {
  return !(m.role === 'user' && typeof m.id === 'string' && m.id.startsWith('temp-'))
}

/** The client wrote after the agent's newest message (false when no agent wrote). */
export function answeredAgent(rows: readonly Row[]): boolean {
  const kept = rows.filter(stored)
  let last = -1
  kept.forEach((m, i) => { if (m.role === 'agent') last = i })
  return last !== -1 && kept.slice(last + 1).some(m => m.role === 'user')
}

/** Whether to ask the server now. `busy`: a send or a reply on its way, a message being typed, the agent typing. */
export function mayAsk(rows: readonly Row[], why: Leaving, s: { busy: boolean; field: FieldState }): boolean {
  if (s.busy || s.field === 'typed') return false
  // An open field on a quiet page asks already; closing the chat or leaving the page with it empty is leaving.
  if (s.field === 'empty' && why === 'idle') return false
  const kept = rows.filter(stored)
  if (!kept.some(m => m.role === 'user')) return false
  if (kept.some(m => m.role === 'agent')) return why !== 'idle' && !answeredAgent(kept)
  return true
}
