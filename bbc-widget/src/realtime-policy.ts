/**
 * PR-10 — how often the widget is allowed to talk to the server.
 *
 * Pure functions so the policy is testable without a DOM.
 */

/** Visitor typing: at most once a second, and only when the text changed. */
export const TYPING_MIN_GAP_MS = 1000

export function shouldSendTyping(
  text: string,
  lastSentText: string,
  lastSentAt: number,
  now: number,
): boolean {
  if (text === lastSentText) return false
  return now - lastSentAt >= TYPING_MIN_GAP_MS
}

/**
 * SSE retry after the browser's own reconnects failed: 5 s, 10 s, 20 s …
 * capped at 60 s. While SSE is down the 1 s poll covers messages and typing;
 * when it reconnects the poll stops.
 */
export const SSE_RETRY_BASE_MS = 5_000
export const SSE_RETRY_MAX_MS = 60_000

export function sseRetryDelay(attempt: number): number {
  const n = Math.max(0, Math.floor(attempt))
  return Math.min(SSE_RETRY_BASE_MS * 2 ** n, SSE_RETRY_MAX_MS)
}

/** An operator typing frame expires on its own if no refresh arrives. */
export const AGENT_TYPING_EXPIRE_MS = 12_000
