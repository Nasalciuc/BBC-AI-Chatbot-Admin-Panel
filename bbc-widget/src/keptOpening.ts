/**
 * The card's opening kept with the open panel (sessionStorage `bbc_widget`) while the visitor has not
 * answered it: the next page of the site shows the same words, and the conversation still starts with
 * the visitor's answer. Without it that page would open a chat with nothing behind it, and ChatWindow
 * would start one on the server (/chat/start) that nobody answered.
 *
 * Pure (no DOM, no storage): Widget.tsx reads and writes the value; tests/proactive.test.mjs tests these.
 */
import type { ProactiveOpening } from './proactive'
import type { OpeningTrigger } from './triggers'
import type { TripPayload } from './tripContext'

// Every OpeningTrigger (triggers.ts): the four moments and the tapped note. Kept here, not imported:
// this file has no runtime imports, so the node tests load it as it is.
const OPENING_TRIGGERS: readonly OpeningTrigger[] = ['kayak', 'quote', 'reader', 'exit', 'note']

/** The opening as it is kept. The reply tapped on the card is not kept: it went out already. */
export function keepOpening(o: ProactiveOpening): Omit<ProactiveOpening, 'firstReply'> {
  return { trigger: o.trigger, message: o.message, quickReplies: o.quickReplies, trip: o.trip, surface: o.surface, shownAt: o.shownAt }
}

/** A kept opening read back, or null when it is not one (anything else written there). */
export function readKeptOpening(v: unknown): ProactiveOpening | null {
  if (!v || typeof v !== 'object') return null
  const o = v as Record<string, unknown>
  const trigger = OPENING_TRIGGERS.find(t => t === o.trigger)
  if (!trigger || typeof o.message !== 'string' || !o.message.trim()) return null
  if (o.surface !== 'card' && o.surface !== 'panel') return null
  const quickReplies = Array.isArray(o.quickReplies)
    ? o.quickReplies.filter((r): r is string => typeof r === 'string' && r.trim() !== '')
    : []
  return {
    trigger,
    message: o.message,
    quickReplies,
    trip: o.trip && typeof o.trip === 'object' ? (o.trip as TripPayload) : null,
    surface: o.surface,
    shownAt: typeof o.shownAt === 'number' && Number.isFinite(o.shownAt) ? o.shownAt : Date.now(),
  }
}
