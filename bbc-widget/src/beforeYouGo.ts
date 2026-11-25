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

interface Row { role: string }

/** The client wrote after the agent's newest message (false when no agent wrote). */
export function answeredAgent(rows: readonly Row[]): boolean {
  let last = -1
  rows.forEach((m, i) => { if (m.role === 'agent') last = i })
  return last !== -1 && rows.slice(last + 1).some(m => m.role === 'user')
}

/** Whether to ask the server now. `busy`: a send or a reply on its way, a message being typed, the agent typing. */
export function mayAsk(rows: readonly Row[], why: Leaving, s: { busy: boolean; field: FieldState }): boolean {
  if (s.busy || s.field === 'typed') return false
  // An open field on a quiet page asks already; closing the chat or leaving the page with it empty is leaving.
  if (s.field === 'empty' && why === 'idle') return false
  if (!rows.some(m => m.role === 'user')) return false
  if (rows.some(m => m.role === 'agent')) return why !== 'idle' && !answeredAgent(rows)
  return true
}
