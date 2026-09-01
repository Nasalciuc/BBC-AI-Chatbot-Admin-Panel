export const IDLE_BADGE_MIN = 15
export const IDLE_NUDGE_MIN = 30

interface IdleInput {
  mode?: string
  assigned_agent_id?: string | null
  last_user_message_at?: string | null
  last_agent_message_at?: string | null
}

/**
 * Minutes since the last message from EITHER side, or null when the question
 * does not apply.
 *
 * Only human-mode conversations with an assigned agent count. An AI-handled
 * chat whose client went quiet is the "Diana" class — a lead the bot is still
 * working — not a desk somebody walked away from, and marking it idle would
 * push agents to close exactly the conversations #200 exists to save.
 */
export function idleMinutes(conv: IdleInput, now: number = Date.now()): number | null {
  if (conv.mode !== 'human' || !conv.assigned_agent_id) return null
  const stamps = [conv.last_user_message_at, conv.last_agent_message_at]
    .filter(Boolean)
    .map((t) => new Date(t as string).getTime())
    .filter((n) => Number.isFinite(n))
  if (!stamps.length) return null
  return Math.floor((now - Math.max(...stamps)) / 60_000)
}
