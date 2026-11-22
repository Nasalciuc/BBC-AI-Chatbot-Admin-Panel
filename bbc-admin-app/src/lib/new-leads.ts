/**
 * A lead given to me (the follow-up lottery, or a manager by hand): when the panel announces it, what it says,
 * and the Tasks badge, which counts it with the late tasks. Pure, so the node tests read it
 * (tests/lottery-view.test.ts). Server half: the heartbeat's followups_new and followup_newest
 * (bbc-chatbot-api app/api/agent.py, contracts/redis-keys.md).
 */

export interface LeadBeat {
  /** When the last lead I've seen was given (unix seconds); null until the first beat that could say (the
   *  baseline: what was there at sign-in is not news). See nextSeen. */
  seen: number | null
  /** The lead given to me last that I haven't opened, and when it was given; null when there is none or the
   *  server can't say. */
  newest: string | null
  newestAt: number | null
  /** The chat on screen: the lead I'm looking at needs no alert. */
  viewing: string | null
}

/** The lead to announce on this beat, or null: the newest, when it was given after the last one seen. By when
 *  it was given, not by the count (CodeRabbit #273): opening A while B arrives leaves the count as it was, and
 *  B is news; opening the newest makes an older one newest, and that is not. Once seen, never again. */
export function leadToAnnounce(b: LeadBeat): string | null {
  if (b.seen === null || b.newest === null || b.newestAt === null) return null
  if (b.newestAt <= b.seen || b.newest === b.viewing) return null
  return b.newest
}

/** What has been seen after this beat. The newest's time when the server says it. None given (a count of 0)
 *  keeps what was seen, 0 at sign-in: the next lead is news all the same, because the server scores every lead
 *  after the last one given to this agent, empty badge or not (bbc-chatbot-api lead_lottery.BADGE_ADD_LUA, its
 *  floor). A count without its newest (the read failed) keeps what was seen too, so the lead is announced when
 *  the read comes back (CodeRabbit #273). */
export function nextSeen(seen: number | null, count: number | null, newestAt: number | null): number | null {
  if (newestAt !== null) return Math.max(seen ?? newestAt, newestAt)
  if (count === 0) return seen ?? 0
  return seen
}

/** What the toast and the browser notification say; `n` is how many came since the last beat. */
export function newLeadText(n: number): { title: string; body: string } {
  return {
    title: n > 1 ? `${n} new leads given to you` : 'New lead given to you',
    body: 'A client with a trip and a contact is waiting for your offer.',
  }
}

/** Where Open goes: the chat itself (the list reads ?highlight= at mount), and opening it confirms the lead. */
export function leadHref(conversationId: string): string {
  return `/chats?highlight=${encodeURIComponent(conversationId)}`
}

/** The Tasks item's badge: the late tasks and the leads given to me that I haven't opened, together, and a
 *  label that names each. It used to show the late tasks alone, and the new leads, which go back to the
 *  lottery 15 minutes after they're given, not at all. */
export function tasksBadge(late: number, leads: number | null): { text: string; label: string } | null {
  const t = late > 0 ? late : 0
  const l = leads !== null && leads > 0 ? leads : 0
  if (t + l === 0) return null
  const parts: string[] = []
  if (t) parts.push(`${t} late`)
  if (l) parts.push(`${l} new lead${l === 1 ? '' : 's'} given to you`)
  return { text: String(t + l), label: parts.join(' · ') }
}
