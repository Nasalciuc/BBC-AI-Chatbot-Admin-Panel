/**
 * A lead given to me (the follow-up lottery, or a manager by hand): when the panel announces it, what it says,
 * and the Tasks badge, which counts it with the late tasks. Pure, so the node tests read it
 * (tests/lottery-view.test.ts). Server half: the heartbeat's followups_new and followup_newest
 * (bbc-chatbot-api app/api/agent.py, contracts/redis-keys.md).
 */

export interface LeadBeat {
  /** The count on the last beat that had one; null before the first (the baseline). */
  before: number | null
  /** This beat's count; null when the server can't say. */
  count: number | null
  /** The lead given to me last that I haven't opened; null when there is none or the server can't say. */
  newest: string | null
  /** Leads announced already, and those there at the first count: never announced (again). */
  announced: ReadonlySet<string>
  /** The chat on screen: the lead I'm looking at needs no alert. */
  viewing: string | null
}

/** The lead to announce on this beat, or null. Only when the count grew since a beat that had one (the first
 *  is the baseline: what was there at sign-in is not news) and the newest lead is one not announced yet. The
 *  same lead never twice, and opening one (the count drops) announces nothing. */
export function leadToAnnounce(b: LeadBeat): string | null {
  if (b.before === null || b.count === null || b.newest === null) return null
  if (b.count <= b.before || b.announced.has(b.newest) || b.newest === b.viewing) return null
  return b.newest
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
