/**
 * The card's opening kept with the open panel (sessionStorage `bbc_widget`) while the visitor has not
 * answered it: the next page of the site shows the same words, and the conversation still starts with
 * the visitor's answer. Without it that page would open a chat with nothing behind it, and ChatWindow
 * would start one on the server (/chat/start) that nobody answered.
 *
 * Pure (no DOM, no storage): Widget.tsx reads and writes the value; tests/proactive.test.mjs tests these.
 */
import type { ChatButton, ChannelKind } from './channel'
import type { ProactiveOpening } from './proactive'
import type { OpeningTrigger } from './triggers'
import type { TripPayload } from './tripContext'

// Every OpeningTrigger (triggers.ts): the five moments and the tapped note. Kept here, not imported:
// this file has no runtime imports, so the node tests load it as it is.
const OPENING_TRIGGERS: readonly OpeningTrigger[] = ['kayak', 'ads', 'quote', 'reader', 'exit', 'note']

/** The opening as it is kept. The reply tapped on the card is not kept: it went out already; nor the channel
 *  picked before the chat opened (its field was on that page). What follows the card stays with it: the
 *  answer to its yes, its channel button, the row's buttons, its language and its C4 side. */
export function keepOpening(o: ProactiveOpening): Omit<ProactiveOpening, 'firstReply' | 'firstChannel'> {
  return {
    trigger: o.trigger, message: o.message, quickReplies: o.quickReplies, trip: o.trip, surface: o.surface, shownAt: o.shownAt,
    ...(o.variant ? { variant: o.variant } : {}),
    ...(o.confirm ? { confirm: o.confirm } : {}),
    ...(o.channel ? { channel: o.channel } : {}),
    ...(o.buttons ? { buttons: o.buttons } : {}),
    ...(o.lang ? { lang: o.lang } : {}),
    ...(o.c4 ? { c4: o.c4 } : {}),
  }
}

// What a kept button may be (api.ts readButtons, repeated: no runtime imports here).
const BUTTON_KINDS = ['whatsapp', 'email', 'wait', 'change']

function keptButtons(v: unknown): ChatButton[] {
  if (!Array.isArray(v)) return []
  return v.filter((b): b is ChatButton =>
    !!b && typeof b === 'object' && BUTTON_KINDS.includes((b as ChatButton).kind)
    && typeof (b as ChatButton).label === 'string' && (b as ChatButton).label.trim() !== '')
    .map(b => ({ kind: b.kind, label: b.label }))
}

/** The variant as the server names them (api.ts offerVariant, repeated: no runtime imports here). */
const VARIANT = /^[a-z][a-z_]{0,23}$/

/** A kept opening read back, or null when it is not one (anything else written there). */
export function readKeptOpening(v: unknown): ProactiveOpening | null {
  if (!v || typeof v !== 'object') return null
  const o = v as Record<string, unknown>
  const trigger = OPENING_TRIGGERS.find(t => t === o.trigger)
  if (!trigger || typeof o.message !== 'string' || !o.message.trim()) return null
  if (o.surface !== 'card' && o.surface !== 'panel' && o.surface !== 'row') return null
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
    ...(typeof o.variant === 'string' && VARIANT.test(o.variant) ? { variant: o.variant } : {}),
    ...keptAfter(o),
  }
}

function keptAfter(o: Record<string, unknown>): Partial<ProactiveOpening> {
  const out: Partial<ProactiveOpening> = {}
  const confirm = o.confirm as { message?: unknown; buttons?: unknown } | undefined
  const confirmButtons = keptButtons(confirm?.buttons)
  if (confirm && typeof confirm.message === 'string' && confirm.message.trim() && confirmButtons.length) {
    out.confirm = { message: confirm.message, buttons: confirmButtons }
  }
  const [channel] = keptButtons([o.channel])
  if (channel && (channel.kind === 'whatsapp' || channel.kind === 'email')) {
    out.channel = channel as ChatButton & { kind: ChannelKind }
  }
  const buttons = keptButtons(o.buttons)
  if (buttons.length) out.buttons = buttons
  if (o.lang === 'es' || o.lang === 'fr') out.lang = o.lang
  if (o.c4 === 'early' || o.c4 === 'now') out.c4 = o.c4
  return out
}
