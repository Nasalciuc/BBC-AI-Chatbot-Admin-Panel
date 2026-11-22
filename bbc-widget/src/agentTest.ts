/**
 * The owner's agent-first test (22 Nov 2026; bbc-chatbot-api app/services/agent_first_test.py): half the visitors
 * are the agent's side, listed in "On the site now", and Alex's card waits for an agent who may say hello; the
 * other half is Alex's, as before. The server decides the side and names it on its answers ("test"); this keeps
 * it for the visit and puts it on every PostHog event the widget sends (posthog.ts), so the result reads per side.
 *
 * Also the time Alex's card may come (the end of its wait, unix seconds), sent with each beat so the agent sees
 * how long they have, and whether Alex's card was shown on this visit before an agent's hello.
 *
 * No imports: the tests run it in node. Storage refused: it all lives in this page's memory.
 */

export type AgentTestSide = 'agent' | 'alex'

const SIDE_KEY = 'bbc_agent_test'
const ALEX_AT_KEY = 'bbc_alex_at'
const WAIT_UNTIL_KEY = 'bbc_alex_wait_until'
const ALEX_SHOWN_KEY = 'bbc_alex_shown'
/** The server's own cap on Alex's wait (agent_first_test.MAX_HOLD_S). */
export const MAX_WAIT_S = 600

let sideInPage: AgentTestSide | null | undefined
let alexAtInPage: number | null | undefined
let waitUntilInPage: number | null | undefined
let alexShownInPage = false

function ssGet(k: string): string | null { try { return sessionStorage.getItem(k) } catch { return null } }
function ssSet(k: string, v: string) { try { sessionStorage.setItem(k, v) } catch { /* this page's memory holds it */ } }
function ssRemove(k: string) { try { sessionStorage.removeItem(k) } catch { /* this page's memory holds it */ } }

/** Only the two sides the server names; anything else is no side. */
export function readSide(v: unknown): AgentTestSide | null {
  return v === 'agent' || v === 'alex' ? v : null
}

/** A server answer came: its `test` is this visit's side; an answer without one means the test is off. */
export function noteSide(v: unknown): void {
  const s = readSide(v)
  sideInPage = s
  if (s) ssSet(SIDE_KEY, s)
  else ssRemove(SIDE_KEY)
}

/** The visit's side, or null (no test, or no answer yet on this visit). */
export function side(): AgentTestSide | null {
  if (sideInPage !== undefined) return sideInPage
  return readSide(ssGet(SIDE_KEY))
}

/** What every PostHog event the widget sends carries: the side, while there is one. */
export function agentTestProps(): { agent_test?: AgentTestSide } {
  const s = side()
  return s ? { agent_test: s } : {}
}

/** The seconds a "wait" answer gives, held to the server's cap; null for anything else. */
export function waitSeconds(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) && v >= 1 && v <= MAX_WAIT_S ? Math.ceil(v) : null
}

/** Alex waits `seconds` from now. The next ask waits by this browser's clock; the beat says when he comes by the
 *  server's (`serverAt`, unix seconds), so the agent's countdown doesn't hang on the visitor's clock. */
export function holdAlex(seconds: number, serverAt?: number | null, nowMs: number = Date.now()): number {
  const until = nowMs + seconds * 1000
  waitUntilInPage = until
  ssSet(WAIT_UNTIL_KEY, String(until))
  const at = typeof serverAt === 'number' && Number.isInteger(serverAt) && serverAt > 0
    ? serverAt : Math.ceil(nowMs / 1000) + seconds
  alexAtInPage = at
  ssSet(ALEX_AT_KEY, String(at))
  return at
}

/** When Alex's card may come (unix seconds, the server's clock), or null when he waits for nobody on this visit. */
export function alexAt(): number | null {
  if (alexAtInPage !== undefined) return alexAtInPage
  const n = Number(ssGet(ALEX_AT_KEY))
  return Number.isInteger(n) && n > 0 ? n : null
}

/** Milliseconds Alex still waits by this browser's clock, 0 when he doesn't. */
export function alexWaitLeftMs(nowMs: number = Date.now()): number {
  let until = waitUntilInPage
  if (until === undefined) {
    const n = Number(ssGet(WAIT_UNTIL_KEY))
    until = Number.isFinite(n) && n > 0 ? n : null
  }
  return until ? Math.max(0, until - nowMs) : 0
}

/** Alex's card showed on this visit (an agent's hello after it is a second word, measured as such). */
export function noteAlexShown(): void {
  alexShownInPage = true
  ssSet(ALEX_SHOWN_KEY, '1')
}

export function alexShown(): boolean {
  return alexShownInPage || ssGet(ALEX_SHOWN_KEY) === '1'
}

/** Tests only: forget this page's memory. */
export function resetAgentTestForTests(): void {
  sideInPage = undefined
  alexAtInPage = undefined
  waitUntilInPage = undefined
  alexShownInPage = false
}
