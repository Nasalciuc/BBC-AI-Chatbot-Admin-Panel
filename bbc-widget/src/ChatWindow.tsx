import { useState, useEffect, useRef } from 'preact/hooks'
import { shouldSendTyping, sseRetryDelay, AGENT_TYPING_EXPIRE_MS } from './realtime-policy'
import { ACTIVITY_EVENTS, BEAT_MS, idleSeconds, isMobileDevice, shouldBeat, signalQuery } from './presence-policy'
import type { PresenceSignal } from './presence-policy'
import {
  apiFetch, askBeforeYouGo, ensureVisitorToken, getVisitorId, getVisitorToken, readButtons, saveVisitorToken,
  type OfferConfirm,
} from './api'
import { guessCountry, readablePhone, typedEmail, validEmail, validPhone, type ButtonKind, type ChannelKind } from './channel'
import { COUNTRIES, type Country } from './countries'
import { ChannelIcon } from './FloatingButtons'
import { browserLanguage, channelMessage, timeZone, widgetLang, words } from './i18n'
import { useKeyboardInset } from './keyboard'
import { getTurnstileToken } from './turnstile'
import type { FormCheck } from './TunnelForm'
import brand from './config'
import { LOCAL_AI_PREFIX, promoteTemp, reconcileBatch } from './reconcile'
import { PanelTop } from './PanelTop'
import { PersonaAvatar } from './PersonaAvatar'
import { useLarge } from './largeView'
import { IconArrowDown, IconArrowUp } from './icons'
import { richText } from './richText'
import { useIsPhone } from './phoneSheet'
import { posthog, posthogSessionId } from './posthog'
import { contactTestProps, markContactLeft, markProactiveConversation, type ProactiveOpening } from './proactive'
import type { LiveHello } from './liveRead'
import { siteMetadata } from './siteSignals'
import { isNotNow, keysOnSite } from './triggers'
import {
  NOT_YET, NOT_YET_WAIT_MS, markAfter, markAllowsAsk, mayAsk, pendingMark, type FieldState, type Leaving,
} from './beforeYouGo'
import { CONSULTANT_LABEL, agentInitials, currentOperator, withOperatorFrom } from './operator'
import { createReplyTurns } from './replyTurns'

interface Message {
  id: string
  role: 'user' | 'ai' | 'agent' | 'system'
  content: string
  created_at: string
  /** Operator identity — present on role='agent' messages only. */
  agent_name?: string | null
  agent_avatar_url?: string | null
  /** A send of the client's that was a button (kept for Retry; never from the server). */
  sendOpts?: SendOptions
}

/** A reply offered under the last message: a label, and what it does when the server said (channel.ts) —
 *  a channel opens its field, "wait" and the yes go out as buttons the server answers without the model. */
interface Chip {
  label: string
  kind?: ButtonKind
}

const asChips = (labels: string[]): Chip[] => labels.map(label => ({ label }))

/** A button the server answers without the model (app/pipeline/card_buttons.py). */
type ServerButton = 'yes' | 'sms' | 'email' | 'wait'

interface SendOptions {
  button?: ServerButton
  /** The answer already known (the card's yes, A2): shown at once, then swapped for the stored row. */
  local?: OfferConfirm
  /** The price the client saw, typed beside the channel (C5): for the agent, never repeated. */
  priceSeen?: string
}

// The id of an answer shown before the server stored it (the card's yes, A2) is LOCAL_AI_PREFIX + a time:
// reconcile.ts swaps it for the stored row, by its words, whichever comes first — the send's answer or the poll.
// sessionStorage: B1 was asked for this conversation in this tab (the server says it once per conversation).
const BEFORE_YOU_GO_KEY = 'bbc_before_you_go'
// B1: this long without anything done on the page, with the chat open, counts as leaving (ADR-6d).
const IDLE_LEAVING_MS = 30_000


interface Props {
  tunnel: 'sales' | 'support'
  visitor: { name?: string; email?: string; phone?: string; country_code?: string }
  metadata?: { booking_id?: string }
  /** PR-12: the pre-chat form's trap, sent with the first /chat/start. */
  formCheck?: FormCheck
  onClose: () => void
  apiUrl: string
  /** CRM iframe mode: fill the container, hide the close ✕ (panel is always open). */
  embedded?: boolean
  /** The chat took the form's place in an open panel: the panel does not enter again. */
  swapped?: boolean
  /**
   * Shown or tucked away. Closing the chat no longer unmounts it: the conversation keeps its connection,
   * finishes what it was sending and keeps receiving, so nothing is lost and a reply that lands while
   * it is closed can be announced on the corner button.
   */
  visible?: boolean
  /** Typed into the message box of a new conversation, from the page the visitor is on (never sent). */
  draft?: string
  /** Messages the visitor has not seen (chat closed or tab in the background), for the corner button. */
  onUnread?: (u: Unread) => void
  /**
   * The chat the proactive card opened (no form): the server's words for the page's trip, the replies and
   * the trip itself. There is no /chat/start: the visitor's first message creates the conversation
   * (/chat/init with `trip` and `proactive`), so a visitor who never answers leaves nothing behind.
   */
  proactive?: ProactiveOpening | null
  /** "Not now", or ✕ before the conversation existed: the card stays away for 24 h. */
  onProactiveDismiss?: (how: 'not_now' | 'close') => void
  /** The visitor's first answer to the card (a button or typed text). */
  onProactiveEngaged?: (reply: string) => void
  /**
   * "On the site now" (live.ts): an agent's hello for a visitor with no chat yet. It shows as the agent's first
   * words, in the place of Alex's opening; nothing exists on the server until the visitor writes, and that first
   * message starts the chat with the hello's tap (/chat/init `live_tap`), already the agent's.
   */
  live?: LiveHello | null
  /** ✕ before the visitor wrote: their "not now" to the agent. */
  onLiveDismiss?: () => void
  /** The first message went with the hello's tap; `held`: the chat started as the agent's. */
  onLiveReplied?: (held: boolean) => void
  /** A first message carries the hello's tap (true) until it lands or fails (false): no "not now" meanwhile. */
  onLiveSending?: (on: boolean) => void
}

export interface Unread {
  count: number
  /** True when the count just grew from a message that arrived live (not history loaded with the page). */
  fresh: boolean
  preview?: { name: string; text: string; initials: string; avatar?: string | null; isAgent: boolean }
}

// Tab-scoped memory of what the visitor typed but did not send yet, and of messages waiting their turn:
// both survive closing the chat and moving to another page of the site.
const DRAFT_KEY = 'bbc_draft'
const QUEUE_KEY = 'bbc_queue'
// Up to which moment this conversation has been seen in an open chat (per conversation, across pages).
const SEEN_KEY = 'bbc_seen'
function ssGet(k: string): string | null { try { return sessionStorage.getItem(k) } catch { return null } }
function ssSet(k: string, v: string | null) {
  try { if (v === null || v === '') sessionStorage.removeItem(k); else sessionStorage.setItem(k, v) } catch { /* storage off */ }
}
function readSeen(convId: string | null): number | null {
  if (!convId) return null
  try {
    const v = localStorage.getItem(SEEN_KEY)
    if (!v) return null
    const [cid, ts] = v.split('|')
    return cid === convId ? Number(ts) || 0 : null
  } catch { return null }
}
function writeSeen(convId: string | null, ts: number) {
  if (!convId) return
  try { localStorage.setItem(SEEN_KEY, `${convId}|${ts}`) } catch { /* storage off */ }
}
const timeOfMsg = (m: { created_at: string }) => Date.parse(m.created_at) || 0

/** Read cached conv_id from localStorage. Widget.tsx has already verified
 *  it with the backend on mount — ChatWindow trusts the cache. */
function getCachedConvId(): string | null {
  try { return localStorage.getItem('bbc_conv_id') } catch { return null }
}

/** The conversation's metadata with this visit's PostHog session, for the panel's replay link, and what
 *  the site said about the page and the visitor's steps before the chat (bbc-signals: page_type,
 *  page_activity; codes and step names only, never what the visitor typed). */
function withSession(metadata?: Record<string, unknown>): Record<string, unknown> {
  const sid = posthogSessionId()
  return { ...(metadata || {}), ...(sid ? { posthog_session_id: sid } : {}), ...siteMetadata() }
}

const openingId = (o: ProactiveOpening) => `opening-${o.shownAt}`
const liveId = (h: LiveHello) => `live-${h.id}`
/** An agent's hello as the chat shows it before it is stored: their words, their first name. */
const liveMessage = (h: LiveHello): Message => ({
  id: liveId(h), role: 'agent', content: h.text, agent_name: h.agent, created_at: new Date().toISOString(),
})

export function ChatWindow({
  tunnel, visitor, metadata, formCheck, onClose, apiUrl, embedded = false, swapped = false,
  visible = true, draft = '', onUnread, proactive, onProactiveDismiss, onProactiveEngaged,
  live, onLiveDismiss, onLiveReplied, onLiveSending,
}: Props) {
  const visibleRef = useRef(visible)
  visibleRef.current = visible
  const mountedAtRef = useRef(Date.now())
  const isPhone = useIsPhone()
  // The whole-screen view with bigger text (computers; largeView.ts): set from the panel's top.
  const [large] = useLarge()
  const savedConvId = getCachedConvId()
  // An agent's hello for a visitor with no conversation yet: it takes the opening's place (below), and the first
  // /chat/init carries its tap. One send owns it, as the opening.
  const liveRef = useRef<LiveHello | null>(!savedConvId && live ? live : null)
  // The card's opening, for a visitor with no conversation yet. Fixed at mount: it is what the first
  // /chat/init carries, and it is dropped once the conversation exists (or an agent's hello takes its place).
  const openingRef = useRef<ProactiveOpening | null>(!savedConvId && proactive && !liveRef.current ? proactive : null)

  const [messages, setMessages] = useState<Message[]>(() => {
    if (liveRef.current) return [liveMessage(liveRef.current)]
    const o = openingRef.current
    return o ? [{ id: openingId(o), role: 'ai', content: o.message, created_at: new Date(o.shownAt).toISOString() }] : []
  })
  const [input, setInput] = useState<string>(() => ssGet(DRAFT_KEY) ?? (savedConvId || openingRef.current || liveRef.current ? '' : draft))
  const [sending, setSending] = useState(false)
  const [streamingText, setStreamingText] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const isStreamingRef = useRef(false)
  /** Which send a reply belongs to (replyTurns.ts): a send's answer may mark
   *  its reply streaming only while that reply has not come in, and only the
   *  latest send unlocks sending (17 Nov 2026). One object for the life of
   *  the chat, shared with the stream's handlers. */
  const [turns] = useState(createReplyTurns)
  const [quickReplies, setQuickReplies] = useState<Chip[] | null>(() => {
    const o = openingRef.current
    if (!o || o.firstReply || o.firstChannel || o.firstContact || !o.quickReplies.length) return null
    const chips = asChips(o.quickReplies)
    // C1 in the panel (computers): the card's own channel, before "Not now".
    if (o.channel) {
      const no = chips.findIndex(c => isNotNow(c.label))
      chips.splice(no < 0 ? chips.length : no, 0, { label: o.channel.label, kind: o.channel.kind })
    }
    return chips
  })
  // The widget's own words, in the browser's language (i18n.ts); the server's are already in it.
  const w = words(widgetLang())
  // The opening's tested sentence (C5, "beat"): the channel field offers the price they saw, optional.
  const beatRef = useRef(openingRef.current?.variant === 'beat')
  // The owner's contact test (22 Nov): this chat's side, on the field's events (the opening is gone after the first send).
  const fieldTestRef = useRef(contactTestProps(openingRef.current))
  // The field where the client leaves the contact the agent writes to (channel.ts): SMS or email.
  const [channelForm, setChannelForm] = useState<ChannelKind | null>(() => openingRef.current?.firstChannel ?? null)
  // The field opened by itself with the yes's answer (the contact test's field side): the other channel is offered in it.
  const [autoField, setAutoField] = useState(false)
  // The send whose answer opened that field: only that send takes it away again, and only while it is still the one it
  // opened (CodeRabbit #273: a field the visitor opened since, and what they typed in it, stays).
  const autoFieldOwnerRef = useRef<string | null>(null)
  const [channelValue, setChannelValue] = useState('')
  const [channelPrice, setChannelPrice] = useState('')
  const [channelError, setChannelError] = useState('')
  const [country, setCountry] = useState<Country>(() => guessCountry(COUNTRIES, browserLanguage(), timeZone()))
  const channelInputRef = useRef<HTMLInputElement>(null)
  // The answer already on screen while its send is on its way (A2): no "is typing" under it.
  const [localPending, setLocalPending] = useState(false)
  // B1: the client left a contact in this chat (the form's, or a channel's) — then no "before you go", and no second
  // chance for the row beside a refused form in this visit (proactive.ts markContactLeft).
  const contactGivenRef = useRef(Boolean(visitor.email || visitor.phone))
  const contactGiven = () => {
    contactGivenRef.current = true
    markContactLeft()
  }
  useEffect(() => { if (contactGivenRef.current) markContactLeft() }, [])
  useKeyboardInset(visible && !embedded)
  const [pendingGreeting, setPendingGreeting] = useState(false)
  const [convId, setConvId] = useState<string | null>(savedConvId)
  const logRef = useRef<HTMLDivElement>(null)
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const initialized = useRef(false)
  const typingTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastTypingSentRef = useRef<number>(0)
  const lastTypingTextRef = useRef<string>('')
  // True while the SSE stream is open: the 1 s typing poll stands down (PR-10).
  const sseUpRef = useRef(false)
  const agentTypingExpiryRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const closeSentRef = useRef(false)
  // Last time the visitor did anything on the PAGE (presence-policy.ts): moved, scrolled,
  // typed, touched, in the chat or anywhere else. The server tells "inactive" and "gone"
  // from it; the beats stop after 20 idle minutes and the first touch starts them again.
  const lastActivityRef = useRef(Date.now())
  // Set by the presence effect: a beat now (the chat just opened or closed), and what the
  // first touch does once the beats have stopped.
  const beatNowRef = useRef<() => void>(() => {})
  const onActivityRef = useRef<() => void>(() => {})
  /** Operator-typing state, polled from the backend (name only, never the draft). */
  const [agentTyping, setAgentTyping] = useState<{ is_typing: boolean; name: string }>({
    is_typing: false,
    name: '',
  })
  /** Messages written while the assistant was still answering: they go out, in order, as soon as it finishes
   *  (a button's with it: a channel left while the yes was on its way is still a channel). */
  const [queued, setQueued] = useState<{ id: string; text: string; opts?: SendOptions }[]>(() => {
    try { return JSON.parse(ssGet(QUEUE_KEY) || '[]') } catch { return [] }
  })
  /** The greeting is on its way (fresh sessions): the log shows the assistant typing instead of nothing. */
  const [starting, setStarting] = useState(!savedConvId && !openingRef.current && !liveRef.current)
  /** The visitor scrolled up to read: new messages no longer pull the log down; a button offers to. */
  const atBottomRef = useRef(true)
  const [hasUnseen, setHasUnseen] = useState(false)
  /** Operator photos that failed to load → show initials instead of a broken image. */
  const [brokenAvatars, setBrokenAvatars] = useState<Record<string, boolean>>({})

  // Safety net: after 15s, unlock sending and drop the typing dots (protects
  // against any stuck state). It watches isStreaming too: on 17 Nov 2026 the
  // dots stayed under Alex's finished reply with sending already off, so a
  // net that watched sending alone never ran.
  useEffect(() => {
    if (!sending && !isStreaming) return
    const timeout = setTimeout(() => {
      setSending(false)
      setIsStreaming(false)
      isStreamingRef.current = false
    }, 15000)
    return () => clearTimeout(timeout)
  }, [sending, isStreaming])

  const notifySessionClose = (reason: 'minimized' | 'left', keepalive = false): Promise<void> => {
    if (!convId || closeSentRef.current) return Promise.resolve()
    closeSentRef.current = true
    return apiFetch(`${apiUrl}/api/chat/session/${convId}/close?reason=${reason}`, {
      method: 'POST',
      keepalive,
    }).then(() => undefined, () => undefined)
  }

  // Keep the newest message in view. Scrolls the log itself, never the host page (scrollIntoView would
  // also scroll the site behind the panel on some phones).
  // A visitor reading back up the conversation is never yanked down: the log follows new messages only
  // while it is at the bottom, and otherwise shows a "New message" button.
  // While the log scrolls itself down (smoothly), the scroll events on the way are not the visitor reading
  // back up: an answer that lands mid-way (the card's yes, a channel's: a few hundred ms apart) still pulls
  // the log down instead of hiding behind "New message".
  const autoScrollUntilRef = useRef(0)
  const scrollToEnd = (smooth = true) => {
    const el = logRef.current
    if (!el) return
    atBottomRef.current = true
    setHasUnseen(false)
    autoScrollUntilRef.current = smooth ? Date.now() + 700 : 0
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
  }
  const onLogScroll = () => {
    const el = logRef.current
    if (!el) return
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 72
    if (!near && Date.now() < autoScrollUntilRef.current) return
    atBottomRef.current = near
    if (near) setHasUnseen(false)
  }
  useEffect(() => {
    if (atBottomRef.current) scrollToEnd(messages.length > 1)
    else setHasUnseen(true)
  }, [messages.length, streamingText, sending, agentTyping.is_typing, queued.length])
  // The visitor's own hand on the log — a wheel or a finger moving it, a key that scrolls up — ends that
  // window at once: the scroll that follows says where they are, and the next streamed chunk leaves them
  // there (CodeRabbit #270: each chunk renewed the 700 ms, so reading up mid-answer was pulled back down).
  // The log's own smooth scroll sends none of these. Passive: scrolling never waits for them.
  useEffect(() => {
    const el = logRef.current
    if (!el) return
    const stop = () => { autoScrollUntilRef.current = 0 }
    const onWheel = (e: WheelEvent) => { if (e.deltaY < 0) stop() }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'ArrowUp' || e.key === 'PageUp' || e.key === 'Home') stop() }
    el.addEventListener('wheel', onWheel, { passive: true })
    el.addEventListener('touchmove', stop, { passive: true })
    el.addEventListener('keydown', onKey)
    return () => {
      el.removeEventListener('wheel', onWheel)
      el.removeEventListener('touchmove', stop)
      el.removeEventListener('keydown', onKey)
    }
  }, [])

  // ── The visitor doing something on the page (presence-policy.ts) ───────────
  // Anywhere on it, not only in the chat. Capture and passive: nothing on the page is
  // delayed or blocked, and a handler that stops propagation does not hide it.
  useEffect(() => {
    const opts: AddEventListenerOptions = { capture: true, passive: true }
    const on = () => {
      lastActivityRef.current = Date.now()
      onActivityRef.current()
    }
    for (const e of ACTIVITY_EVENTS) window.addEventListener(e, on, opts)
    return () => { for (const e of ACTIVITY_EVENTS) window.removeEventListener(e, on, opts) }
  }, [])

  // ── Open and closed (the chat stays mounted) ─────────────────────────────
  // Closing tells the server the client minimized the chat (no heartbeat until it opens again) and
  // clears the operator's live preview of an unsent draft; opening marks the client back, then shows
  // the end of the conversation and puts the cursor in the message box.
  const wasVisibleRef = useRef(visible)
  useEffect(() => {
    const was = wasVisibleRef.current
    wasVisibleRef.current = visible
    if (visible === was) return
    if (visible) {
      if (convId) {
        closeSentRef.current = false
        // Then a beat that says the chat is open (the server's presence line follows at once).
        apiFetch(`${apiUrl}/api/chat/session/${convId}/open`, { method: 'POST' }).catch(() => {})
          .finally(() => beatNowRef.current())
      }
      requestAnimationFrame(() => { scrollToEnd(false); fitComposer(); focusComposer() })
    } else {
      // Still on the site with the chat closed: a beat says so once the close is written.
      notifySessionClose('minimized', true).finally(() => beatNowRef.current())
      if (convId) apiFetch(`${apiUrl}/api/chat/typing/${convId}`, { method: 'DELETE' }).catch(() => {})
      // B1: closing the chat without a contact is leaving it; the corner button shows the message.
      beforeYouGoRef.current('closed')
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible])

  // ── Unread: messages that arrived while the chat was closed or the tab was in the background ─────
  const [docVisible, setDocVisible] = useState(() => {
    try { return document.visibilityState === 'visible' } catch { return true }
  })
  useEffect(() => {
    const on = () => setDocVisible(document.visibilityState === 'visible')
    document.addEventListener('visibilitychange', on)
    return () => document.removeEventListener('visibilitychange', on)
  }, [])
  const lastUnreadRef = useRef(0)
  useEffect(() => {
    if (!onUnread) return
    const rows = messages.filter(m =>
      m && typeof m.id === 'string' && typeof m.content === 'string' && m.content.trim() !== '')
    if (!rows.length) return
    const latest = Math.max(...rows.map(timeOfMsg))
    if (visible && docVisible) {
      writeSeen(convId, latest)
      if (lastUnreadRef.current !== 0) {
        lastUnreadRef.current = 0
        onUnread({ count: 0, fresh: false })
      }
      return
    }
    let seen = readSeen(convId)
    if (seen === null) {
      // Nothing recorded for this conversation: what is already here counts as read.
      writeSeen(convId, latest)
      seen = latest
    }
    // Only words from the assistant or a person count (not notes like "Lana joined the chat").
    const unseen = rows.filter(m => (m.role === 'ai' || m.role === 'agent') && timeOfMsg(m) > (seen as number))
    const count = unseen.length
    // Fresh = a message that just arrived (not history loaded with the page): it earns the chime.
    const fresh = count > lastUnreadRef.current && Date.now() - mountedAtRef.current > 2500
    lastUnreadRef.current = count
    const last = unseen[unseen.length - 1]
    const name = !last ? '' : last.role === 'agent'
      ? ((last.agent_name || '').trim() || CONSULTANT_LABEL)
      : brand.persona.name
    onUnread({
      count,
      fresh,
      preview: last ? {
        name,
        text: last.content,
        initials: agentInitials(name),
        avatar: last.role === 'agent' ? (last.agent_avatar_url || null) : null,
        isAgent: last.role === 'agent',
      } : undefined,
    })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages, visible, docVisible, convId])

  // The composer takes focus on computers; on touch screens the keyboard waits for a tap. Never out of a field
  // of the site: a panel that opened by itself while a name was being typed took the last keys (22 Nov).
  const focusComposer = () => {
    try {
      if (!window.matchMedia('(pointer: fine)').matches) return
      if (keysOnSite(document.activeElement)) return
      const el = composerRef.current
      if (!el) return
      el.focus({ preventScroll: true })
      el.setSelectionRange(el.value.length, el.value.length)
    } catch { /* matchMedia unavailable — leave focus where it is */ }
  }
  useEffect(() => {
    if (visible) focusComposer()
    fitComposer()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // What the visitor typed and what waits its turn are kept for this tab: closing the chat, a reload or
  // another page of the site never loses them.
  useEffect(() => { ssSet(DRAFT_KEY, input) }, [input])
  useEffect(() => { ssSet(QUEUE_KEY, queued.length ? JSON.stringify(queued) : null) }, [queued])

  /** Grow the composer with its text, up to its max height (CSS). */
  const fitComposer = () => {
    const el = composerRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight + 2}px`
  }

  // The AI greets FIRST: create the conversation and render the armed,
  // personalized opening as the first bubble. No fake client message.
  const startConversation = async () => {
    try {
      const turnstile_token = await getTurnstileToken()
      const res = await apiFetch(`${apiUrl}/api/chat/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tunnel,
          visitor: {
            name: visitor.name || null,
            email: visitor.email || null,
            phone: visitor.phone || null,
            country_code: visitor.country_code || null,
          },
          metadata: withSession(metadata),
          visitor_id: getVisitorId(),
          turnstile_token,
          form_check: formCheck,
        }),
      })
      if (!res.ok) throw new Error('start failed')
      const data = await res.json()
      saveVisitorToken(data.visitor_token)
      if (!data.conversation_id) return
      setConvId(data.conversation_id)
      apiFetch(`${apiUrl}/api/chat/session/${data.conversation_id}/open`, { method: 'POST' }).catch(() => {})
      try { localStorage.setItem('bbc_conv_id', data.conversation_id) } catch {}
      try { localStorage.setItem('bbc_conv_ts', Date.now().toString()) } catch {}
      if (data.message) {
        const aiMsg: Message = {
          id: `ai-${Date.now()}`,
          role: 'ai',
          content: data.message,
          created_at: new Date().toISOString(),
        }
        setMessages(prev => [...prev, aiMsg])
        // Checkpoint past the greeting so the next poll doesn't duplicate it
        lastMsgTime.current = new Date(Date.now() + 2000).toISOString()
      }
    } catch {
      // Fail quiet — the client can still type; their first message flows
      // through the normal send path and creates the conversation.
    } finally {
      setStarting(false)
    }
  }

  // AI opens on fresh sessions — SKIP if restored session
  useEffect(() => {
    if (initialized.current) return
    initialized.current = true
    // The card opened this chat: its words are on screen and nothing exists on the server until the
    // visitor answers. A button tapped on the phone card is that answer.
    if (openingRef.current) {
      const o = openingRef.current
      const first = o.firstReply
      if (first) {
        onProactiveEngaged?.(first)
        // The card's yes on a card that knew the whole trip: its answer is on screen at once (A2).
        sendMessage(first, yesOptions(o, first))
      } else if (o.firstContact) {
        // The row beside the refused form took the contact itself (FormRow.tsx): it goes out as the client's first
        // message, as the channel's field sends it. Only the kind goes to PostHog, never the contact.
        const { kind, text } = o.firstContact
        onProactiveEngaged?.(text)
        posthog?.capture('chat_channel_given', { channel: kind, tunnel, where: 'row', ...fieldTestRef.current })
        sendMessage(text, { button: kind })
      } else if (o.firstChannel) {
        // The card's channel or the pill: the chat opens on the field (nothing sent before it).
        requestAnimationFrame(() => channelInputRef.current?.focus({ preventScroll: true }))
      }
      return
    }
    // The row beside the refused form took the contact while an agent's hello held the opening's place (the hello
    // came after the row was shown): the contact still goes out, as the visitor's answer to the hello, whose tap
    // goes with this send (CodeRabbit #279). Dropped with the opening, it was never sent nor shown.
    const rowContact = liveRef.current ? proactive?.firstContact : undefined
    if (rowContact) {
      onProactiveEngaged?.(rowContact.text)
      posthog?.capture('chat_channel_given', {
        channel: rowContact.kind, tunnel, where: 'row', ...contactTestProps(proactive ?? null),
      })
      sendMessage(rowContact.text, { button: rowContact.kind })
      return
    }
    // An agent's hello is on screen: nothing exists on the server until the visitor answers it.
    if (liveRef.current) return
    if (savedConvId) return
    startConversation()
  // eslint-disable-next-line
  }, [])

  // The row's second chance (proactive.ts, triggers.ts rowAgainOpen) stands beside the site's refused form while this
  // visitor has a conversation: the card's chat they left without a contact. Its contact goes into that conversation,
  // as the channel's field sends it (the server keeps it there too, card_buttons._channel). The opening is only for a
  // visitor with none (openingRef), so without this the contact typed in the row was dropped. Once per opening, and
  // never the one this chat opened with (the mount above sends that one, or its /chat/init does).
  const rowContactSentRef = useRef<ProactiveOpening | null>(savedConvId ? null : proactive ?? null)
  useEffect(() => {
    const o = proactive
    if (!o?.firstContact || !convId || rowContactSentRef.current === o) return
    rowContactSentRef.current = o
    const { kind, text } = o.firstContact
    onProactiveEngaged?.(text)
    // Only the kind goes to PostHog, never the contact.
    posthog?.capture('chat_channel_given', { channel: kind, tunnel, where: 'row', again: true, ...contactTestProps(o) })
    sendOrQueue(text, { button: kind })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [proactive, convId])

  // Verify restored session is still active (runs once at mount)
  useEffect(() => {
    if (!savedConvId) return
    // Presence: the client is "at the widget" only when the chat is open. A conversation kept alive
    // behind a closed chat (another page of the site) does not claim it.
    if (visibleRef.current) {
      apiFetch(`${apiUrl}/api/chat/session/${savedConvId}/open`, { method: 'POST' }).catch(() => {})
    }
    fetch(`${apiUrl}/api/chat/status/${savedConvId}`)
      .then(r => r.json())
      .then(data => {
        if (!data.success || data.data?.status === 'closed') {
          // Conversation closed by agent — but client can still write within 30min session.
          // Next message will automatically reopen the conversation on the backend.
          // Keep localStorage intact so session persists.
          // Do nothing — let client see history and write freely.
        }
      })
      .catch(() => { /* network error — keep session, polling will handle */ })
  // eslint-disable-next-line
  }, [])

  // Re-open with an AI greeting after a closed conversation was detected and cleared
  useEffect(() => {
    if (!pendingGreeting || convId || sending) return
    setPendingGreeting(false)
    startConversation()
  }, [pendingGreeting, convId, sending])

  // Real-time delivery: SSE primary + polling fallback (RxDB checkpoint pattern)
  const lastMsgTime = useRef('')

  useEffect(() => {
    if (!convId) return
    closeSentRef.current = false

    // PRESENCE (presence-policy.ts) — the server decides who is on the site from three
    // signals, all on /ping: a beat every 30 s while the PAGE is in view, chat open or
    // closed; `hidden` when it goes out of view; `leaving` on pagehide. Silence after a
    // signal is what says "left", never a beacon alone: a killed tab, a phone put to sleep
    // or a dropped network send nothing. Presence-only writes — never updated_at (029).
    const mobile = isMobileDevice(typeof navigator === 'undefined' ? undefined : navigator as never)
    // Embedded (the CRM's iframe): the frame cannot see the page around it, so it claims no
    // idleness and its beats never stop for it — the page being in view is what it knows.
    const signal = (kind: PresenceSignal, keepalive = false) => {
      const q = signalQuery(kind, {
        chatOpen: visibleRef.current,
        idleS: embedded ? null : idleSeconds(Date.now(), lastActivityRef.current),
        mobile,
      })
      apiFetch(`${apiUrl}/api/chat/session/${convId}/ping?${q}`, { method: 'POST', keepalive }).catch(() => {})
    }

    let pingInterval: ReturnType<typeof setInterval> | null = null
    // The beats stopped because nobody touched the page for 20 minutes: the first touch beats.
    let beatsPaused = false
    const sendPing = () => {
      if (document.visibilityState !== 'visible') return
      if (!embedded && !shouldBeat(true, Date.now(), lastActivityRef.current)) {
        beatsPaused = true
        return
      }
      beatsPaused = false
      signal('beat')
    }
    // Only a page in view beats. An interval started out of view (mounted in a background tab,
    // or back from the back-forward cache without being shown) would make the moment it comes
    // into view wait for the next tick instead of beating at once.
    const startPing = () => {
      if (pingInterval || document.visibilityState !== 'visible') return
      sendPing()
      pingInterval = setInterval(sendPing, BEAT_MS)
    }
    const stopPing = () => {
      if (!pingInterval) return
      clearInterval(pingInterval)
      pingInterval = null
    }
    beatNowRef.current = sendPing
    onActivityRef.current = () => { if (beatsPaused) sendPing() }

    let leaveSent = false
    // pagehide: the page closed or the visitor followed a link. The next page beats within
    // seconds; the server waits before it calls this a departure.
    const handlePageLeave = () => {
      if (leaveSent) return
      leaveSent = true
      stopPing()
      signal('leaving', true)
    }
    // bfcache return (back button, mobile app-switch): the page left with a `leaving` —
    // mark the client back and re-arm the close beacon.
    const handlePageReturn = () => {
      leaveSent = false
      startPing()
      if (!visibleRef.current) return
      closeSentRef.current = false
      apiFetch(`${apiUrl}/api/chat/session/${convId}/open`, { method: 'POST' }).catch(() => {})
    }
    window.addEventListener('pagehide', handlePageLeave)
    window.addEventListener('pageshow', handlePageReturn)

    // Out of view is the last thing a page is sure to hear, on a phone above all: say it then.
    const handleVisibility = () => {
      if (document.visibilityState === 'visible') {
        startPing()
      } else {
        stopPing()
        signal('hidden', true)
      }
    }
    document.addEventListener('visibilitychange', handleVisibility)
    startPing()

    let source: EventSource | null = null
    let fallbackInterval: ReturnType<typeof setInterval> | null = null
    let errorCount = 0
    const MAX_ERRORS = 3

    // Catch-up poll — syncs missed messages after SSE connect/reconnect
    const catchUpPoll = async () => {
      if (document.visibilityState !== 'visible') return
      try {
        const after = lastMsgTime.current
          ? `?after=${encodeURIComponent(lastMsgTime.current)}`
          : ''
        const res = await apiFetch(`${apiUrl}/api/chat/messages/${convId}${after}`)
        if (!res.ok) return
        const json = await res.json()
        if (json.success && json.data && json.data.length > 0) {
          const incoming = json.data as Message[]
          setMessages(prev => {
            // Reconcile (src/reconcile.ts): the poll may EXPIRE only stale
            // temps — a confirmed send lives as `sent-*` until its server
            // row arrives in a batch and replaces it. The old "drop all
            // temps, hope the batch has them" erased the client's own
            // message whenever the agent-joined burst advanced the ?after=
            // cursor past its timestamp.
            const { next, sawAi } = reconcileBatch(prev, incoming, Date.now())
            if (sawAi) {
              // Polled instead of streamed (SSE down): a reply came in, as
              // with a stream_end. It answers the oldest send still open.
              turns.replyCameIn()
              setIsStreaming(false)
              isStreamingRef.current = false
              setSending(false)
            }
            return next
          })
          lastMsgTime.current = json.data[json.data.length - 1].created_at
        }
      } catch { /* non-fatal */ }
    }

    // Polling fallback — runs only while SSE is down (PR-10). After 3 SSE
    // errors the stream is closed and retried with backoff; the moment it
    // reopens, the poll stops.
    let retryTimer: ReturnType<typeof setTimeout> | null = null
    let retryAttempt = 0
    let disposed = false
    const startPolling = () => {
      if (fallbackInterval) return
      fallbackInterval = setInterval(catchUpPoll, 1000)
    }
    const stopPolling = () => {
      if (!fallbackInterval) return
      clearInterval(fallbackInterval)
      fallbackInterval = null
    }

    // SSE stream — primary real-time channel. v=2: this widget understands
    // the `typing` event; the server sends it only to streams that ask.
    const startSSE = () => {
      // PR-12: EventSource cannot send headers, so the visitor token rides
      // the URL (?vt=). It is short-lived and names only this visitor.
      const vt = getVisitorToken()
      source = new EventSource(
        `${apiUrl}/api/chat/stream/${convId}?vid=${encodeURIComponent(getVisitorId() || '')}&v=2`
        + (vt ? `&vt=${encodeURIComponent(vt)}` : ''),
      )

      source.onopen = () => {
        errorCount = 0        // reset error counter on successful connect
        retryAttempt = 0
        sseUpRef.current = true
        stopPolling()
        catchUpPoll()         // sync messages missed during connect/reconnect
      }

      source.onmessage = (e: MessageEvent) => {
        try {
          const parsed = JSON.parse(e.data)

          // Operator typing (PR-10). Handled BEFORE the generic branch below,
          // which would otherwise render this frame as an empty message.
          if (parsed.event === 'typing') {
            if (agentTypingExpiryRef.current) clearTimeout(agentTypingExpiryRef.current)
            const on = Boolean(parsed.is_typing)
            setAgentTyping({ is_typing: on, name: on ? (parsed.name || '') : '' })
            if (on) {
              agentTypingExpiryRef.current = setTimeout(
                () => setAgentTyping({ is_typing: false, name: '' }),
                AGENT_TYPING_EXPIRE_MS,
              )
            }
            return
          }

          if (parsed.event === 'stream_chunk') {
            setStreamingText(prev => prev + (parsed.delta || ''))
            return
          }

          if (parsed.event === 'stream_end') {
            // A reply is in: the oldest send still open is answered. Its POST
            // answer can still arrive after this frame and must not mark the
            // reply streaming again: "Alex is typing" then stayed for good.
            turns.replyCameIn()
            setStreamingText('')
            setIsStreaming(false)
            isStreamingRef.current = false
            setSending(false)
            const msg = parsed as Message
            setMessages(prev => {
              if (prev.some(m => m.id === msg.id)) return prev
              return [...prev, msg]
            })
            if (msg.created_at) lastMsgTime.current = msg.created_at
            return
          }

          // The stream also carries events that are not messages: the visitor's
          // own typing echoed back ({event:'typing'}), presence, and the like.
          // They have no id and no text; taken as messages they rendered as
          // blank bubbles and, since the redesign, crashed the render (the
          // send button never lit up). Only stored message rows go on.
          if (parsed.event || typeof parsed.id !== 'string' || typeof parsed.content !== 'string') return

          const msg = parsed as Message
          // The server now fans out the visitor's own message too (so the
          // operator's panel sees it). The widget already rendered it locally
          // under a temp- id that will never match the stored UUID, so taking
          // it from the stream would show it twice. A second tab of the same
          // visitor still gets it through the incremental poll.
          if (msg.role === 'user') return
          setMessages(prev => {
            const at = prev.findIndex(m => m.id === msg.id)
            if (at === -1) return [...prev, msg]
            // The same row again: the one on screen stays, signed with the
            // operator's name and photo if only this copy carries them.
            const merged = withOperatorFrom(prev[at], msg)
            return merged === prev[at] ? prev : prev.map((m, k) => (k === at ? merged : m))
          })
          if (msg.created_at) lastMsgTime.current = msg.created_at
        } catch { /* malformed event — ignore */ }
      }

      source.onerror = () => {
        errorCount++
        sseUpRef.current = false
        // An HTTP error (a restart's 502, a 401/403) makes the browser CLOSE
        // the EventSource for good after this one event — it will not retry
        // and onerror never fires again. Treat that as the give-up point at
        // once, or neither the poll nor the retry below would ever start.
        // (An early return for a CLOSED source used to come first: the
        // widget then polled every second for good, never tried SSE again,
        // and kept the stream marked up.)
        const closedForGood = source?.readyState === EventSource.CLOSED
        if (closedForGood || errorCount >= MAX_ERRORS) {
          // The browser's own retries failed: poll meanwhile, and try SSE
          // again later — 5 s, 10 s, 20 s … capped at 60 s.
          source?.close()
          source = null
          startPolling()
          if (retryTimer || disposed) return
          retryTimer = setTimeout(() => {
            retryTimer = null
            if (disposed) return
            errorCount = 0
            ensureVisitorToken(apiUrl, getTurnstileToken).then(() => { if (!disposed) startSSE() })
          }, sseRetryDelay(retryAttempt++))
        }
        // else: browser retries EventSource connection automatically
      }
    }

    catchUpPoll()  // instant load — don't wait for SSE onopen (1-2s delay)
    // A token near expiry is refreshed first: the stream carries it once,
    // in its URL. ensureVisitorToken never throws and is instant when fresh.
    ensureVisitorToken(apiUrl, getTurnstileToken).then(() => { if (!disposed) startSSE() })

    return () => {
      notifySessionClose('minimized', true)
      disposed = true
      sseUpRef.current = false
      source?.close()
      if (retryTimer) clearTimeout(retryTimer)
      if (agentTypingExpiryRef.current) clearTimeout(agentTypingExpiryRef.current)
      if (fallbackInterval) clearInterval(fallbackInterval)
      if (typingTimeoutRef.current) clearTimeout(typingTimeoutRef.current)
      stopPing()
      beatNowRef.current = () => {}
      onActivityRef.current = () => {}
      document.removeEventListener('visibilitychange', handleVisibility)
      window.removeEventListener('pagehide', handlePageLeave)
      window.removeEventListener('pageshow', handlePageReturn)
    }
  }, [convId, apiUrl])

  // ── B1 (ADR-6d): one message as a client with no contact leaves ──────────────────────────────────
  // The tab goes out of view, the chat is closed, or the page goes quiet for IDLE_LEAVING_MS with the chat
  // open — and nobody has the client's contact yet: the server adds one message with the channels
  // (app/pipeline/card_buttons.py, before_you_go), once per conversation. After an agent wrote, only as the
  // client leaves the chat or the page with the agent's newest message unanswered (lever 6, beforeYouGo.ts).
  // Asked once per tab, unless the server says "not yet".
  const messagesRef = useRef(messages)
  messagesRef.current = messages

  // An agent's hello while the chat is open with nothing sent yet (Alex's opening unanswered): their words take the
  // opening's place, so what the visitor answers is what the chat will hold (the hello, stored first). Not over
  // anything the visitor sent (a first message on its way owns the opening, and the chat it makes is Alex's), nor
  // over a greeting being made on the server (/chat/start).
  useEffect(() => {
    if (!live || convId || starting || liveRef.current?.id === live.id) return
    if (messagesRef.current.some(m => m.role === 'user')) return
    liveRef.current = live
    openingRef.current = null
    setMessages([liveMessage(live)])
    setQuickReplies(null)
  }, [live?.id, convId, starting])
  const busyRef = useRef(false)
  busyRef.current = sending || isStreaming || !!input.trim() || agentTyping.is_typing
  // The channel field apart (beforeYouGo.ts mayAsk): left empty, closing the chat or the tab is leaving.
  const fieldRef = useRef<FieldState>('none')
  fieldRef.current = !channelForm ? 'none' : channelValue.trim() ? 'typed' : 'empty'
  // After the server's "not yet" (NOT_YET), a quiet page asks again no sooner than this (ms).
  const notYetUntilRef = useRef(0)
  // This page's ask is out: no second one before its answer (the tab's mark covers the pages after it).
  const askingRef = useRef(false)
  const beforeYouGoRef = useRef<(why: Leaving) => void>(() => {})
  beforeYouGoRef.current = (why: Leaving) => {
    const cid = convId
    if (!cid || embedded || contactGivenRef.current || askingRef.current) return
    const key = `${BEFORE_YOU_GO_KEY}:${cid}`
    if (!markAllowsAsk(ssGet(key), Date.now())) return
    if (why === 'idle' && Date.now() < notYetUntilRef.current) return
    const rows = messagesRef.current
    if (!mayAsk(rows, why, { busy: busyRef.current, field: fieldRef.current })) return
    const afterAgent = rows.some(m => m.role === 'agent')
    askingRef.current = true
    ssSet(key, pendingMark(Date.now()))
    void askBeforeYouGo(apiUrl, cid, why !== 'idle', why).then(out => {
      askingRef.current = false
      // Not now, maybe at the next leave: a "not yet" does not spend the tab's one ask (beforeYouGo.ts markAfter).
      ssSet(key, markAfter(out))
      if (out && !out.show && out.why && NOT_YET.has(out.why)) {
        notYetUntilRef.current = Date.now() + NOT_YET_WAIT_MS
        return
      }
      if (!out?.show || !out.message || !out.id) return
      const msg: Message = {
        id: out.id, role: 'ai', content: out.message, created_at: out.created_at || new Date().toISOString(),
      }
      setMessages(prev => (prev.some(m => m.id === msg.id) ? prev : [...prev, msg]))
      if (out.buttons?.length) setQuickReplies(out.buttons.map(b => ({ label: b.label, kind: b.kind })))
      if (out.created_at && (!lastMsgTime.current || out.created_at > lastMsgTime.current)) {
        lastMsgTime.current = out.created_at
      }
      // How the client left, and whether an agent had written: before and after lever 6 compare on these.
      posthog?.capture('chat_before_you_go', { tunnel, why, ...(afterAgent ? { after_agent: true } : {}) })
    })
  }
  useEffect(() => {
    if (!convId || embedded) return
    const onHidden = () => { if (document.visibilityState === 'hidden') beforeYouGoRef.current('hidden') }
    document.addEventListener('visibilitychange', onHidden)
    const idle = setInterval(() => {
      if (!visibleRef.current || document.visibilityState !== 'visible') return
      if (Date.now() - lastActivityRef.current >= IDLE_LEAVING_MS) beforeYouGoRef.current('idle')
    }, 5_000)
    return () => {
      document.removeEventListener('visibilitychange', onHidden)
      clearInterval(idle)
    }
  }, [convId, embedded])

  /** On a touch screen the keyboard takes a moment to open: then the newest message and the box are in
   *  view (E2, ADR-12b; the sheet itself follows the visible area: phoneSheet.ts, keyboard.ts). */
  const keepComposerInView = () => {
    try { if (window.matchMedia('(pointer: fine)').matches) return } catch { return }
    setTimeout(() => { if (atBottomRef.current) scrollToEnd(false) }, 320)
  }

  // Operator → visitor typing indicator. Primary path: the `typing` frame on
  // the SSE stream (PR-10). This 1 s poll only asks while the stream is down.
  // The backend key has a short TTL, so a stopped operator stops the
  // indicator on its own; the poll only reads.
  useEffect(() => {
    if (!convId) return
    let cancelled = false

    const poll = async () => {
      if (sseUpRef.current) return
      if (document.visibilityState !== 'visible') return
      if (!visibleRef.current) return  // the typing dots are only drawn in an open chat
      try {
        const res = await apiFetch(`${apiUrl}/api/chat/agent-typing/${convId}`)
        if (!res.ok || cancelled) return
        const json = await res.json()
        if (cancelled) return
        const data = json?.data
        setAgentTyping({
          is_typing: Boolean(data?.is_typing),
          name: data?.name || '',
        })
      } catch { /* non-fatal */ }
    }

    poll()
    const id = setInterval(poll, 1000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [convId, apiUrl])

  // An arrived operator message means they finished typing — drop the indicator
  // immediately instead of waiting for the next poll.
  const lastMessageId = messages.length ? messages[messages.length - 1].id : ''
  useEffect(() => {
    if (!messages.length) return
    if (messages[messages.length - 1].role === 'agent') {
      setAgentTyping({ is_typing: false, name: '' })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastMessageId])

  // Send typing event to server.
  //
  // Operator sees the client's typed text in real time. Text stays
  // visible until the client sends / clears input / closes the widget.
  // Previously a 3-second auto-clear timer made the text disappear on
  // pauses — removed because it made it hard for the operator to see
  // what the client was writing during natural pauses (especially for
  // long messages).
  //
  // Clear paths still in place:
  //   1. Input cleared by client → DELETE here (immediately below)
  //   2. Message sent → DELETE in sendMessage() and handleSend()
  //   3. Widget closed / unmounted → no explicit call; backend Redis TTL
  //      (5 min) cleans up orphan state.
  const sendTypingEvent = (text: string) => {
    if (!convId) return

    // Input cleared by client → clear indicator immediately.
    if (!text.trim()) {
      if (typingTimeoutRef.current) {
        clearTimeout(typingTimeoutRef.current)
        typingTimeoutRef.current = null
      }
      lastTypingTextRef.current = ''
      apiFetch(`${apiUrl}/api/chat/typing/${convId}`, {
        method: 'DELETE',
      }).catch(() => {})
      return
    }

    // At most once a second, and only when the text changed (PR-10; was
    // every 200 ms). A trailing send makes sure the operator sees the last
    // keystrokes of a burst, not the text as it was a second earlier.
    const now = Date.now()
    if (typingTimeoutRef.current) {
      clearTimeout(typingTimeoutRef.current)
      typingTimeoutRef.current = null
    }
    if (!shouldSendTyping(text, lastTypingTextRef.current, lastTypingSentRef.current, now)) {
      if (text !== lastTypingTextRef.current) {
        typingTimeoutRef.current = setTimeout(() => {
          typingTimeoutRef.current = null
          sendTypingEvent(text)
        }, Math.max(50, 1000 - (now - lastTypingSentRef.current)))
      }
      return
    }
    lastTypingSentRef.current = now
    lastTypingTextRef.current = text
    apiFetch(`${apiUrl}/api/chat/typing/${convId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    }).catch(() => {})

    // NO auto-clear timer — removed to fix the "text disappears when
    // client pauses" bug. See the comment block at top of this function.
  }

  /** What the card's yes goes out with: the answer the server will store, shown at once (A2), when the card
   *  knew the whole trip and this is its first button. */
  const yesOptions = (o: ProactiveOpening | null, text: string): SendOptions | undefined =>
    o?.confirm && o.quickReplies[0] && text.trim() === o.quickReplies[0].trim()
      ? { button: 'yes', local: o.confirm }
      : undefined

  const sendMessage = async (text: string, opts?: SendOptions) => {
    if (!text.trim()) return
    // This send's number: a late answer to an earlier send (the queue sends
    // the next message as soon as a reply is in) never changes this one.
    const seq = turns.start()
    const isExistingConversation = Boolean(convId)
    lastActivityRef.current = Date.now()
    // Clear typing indicator immediately when sending
    if (convId) {
      if (typingTimeoutRef.current) clearTimeout(typingTimeoutRef.current)
      typingTimeoutRef.current = null
      lastTypingTextRef.current = ''
      apiFetch(`${apiUrl}/api/chat/typing/${convId}`, { method: 'DELETE' }).catch(() => {})
    }
    setSending(true)
    setStreamingText('')

    const optimisticMsg: Message = {
      id: `temp-${Date.now()}`,
      role: 'user',
      content: text,
      created_at: new Date().toISOString(),
      // Retry sends it as it was: the button, the price, and the answer shown at once with the field it opens
      // (CodeRabbit #273: a retried yes on the field side came back without its field).
      ...(opts?.button ? { sendOpts: { button: opts.button, ...(opts.priceSeen ? { priceSeen: opts.priceSeen } : {}), ...(opts.local ? { local: opts.local } : {}) } } : {}),
    }
    // A2: the answer already known goes on screen with the client's tap, with its buttons; the server stores
    // the same words and the stored row takes its place (reconcile.ts).
    const localId = opts?.local ? `${LOCAL_AI_PREFIX}${Date.now()}` : null
    if (opts?.local && localId) {
      const local: Message = {
        id: localId, role: 'ai', content: opts.local.message, created_at: new Date(Date.now() + 1).toISOString(),
      }
      setMessages(prev => [...prev, optimisticMsg, local])
      setQuickReplies(opts.local.buttons.map(b => ({ label: b.label, kind: b.kind })))
      setLocalPending(true)
      // The field side of the owner's contact test: the field opens with the words that ask for it; its buttons
      // (the other channel, "I'll wait here", "Change details") are one Back away. Not over a field already open: a
      // retried yes leaves the one the visitor opened, and what they typed in it, as it is (CodeRabbit #273).
      if (opts.local.field && !channelForm) openChannel(opts.local.field, true, localId)
    } else {
      setMessages(prev => [...prev, optimisticMsg])
    }

    try {
      // Sprint 2: Pre-create conv for SSE streaming on first message
      let activeConvId = convId
      // This send owns the card's opening: a second one racing it (a reply typed while the first is on
      // its way) goes out plain and finds the same conversation, never removing or re-labelling it.
      const opening = convId ? null : openingRef.current
      if (opening) openingRef.current = null
      // So it owns an agent's hello on screen: its tap goes with this send, once.
      const hello = convId ? null : liveRef.current
      if (hello) {
        liveRef.current = null
        onLiveSending?.(true)
      }
      if (!activeConvId) {
        try {
          const turnstile_token = await getTurnstileToken()
          const initRes = await apiFetch(`${apiUrl}/api/chat/init`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              tunnel,
              visitor: {
                name: visitor.name || null,
                email: visitor.email || null,
                phone: visitor.phone || null,
                country_code: visitor.country_code || null,
              },
              metadata: withSession(metadata),
              visitor_id: getVisitorId(),
              turnstile_token,
              form_check: formCheck,
              // An agent's hello: its tap makes the chat theirs, the hello its first message (never with
              // Alex's opening: the hello took its place).
              ...(hello ? { live_tap: hello.tap } : {}),
              // The card's moment and the page's trip: the server writes the opening as the assistant's
              // first message and starts the lead with the trip (unconfirmed). `variant`: the way the
              // server asked the first question (the note's bandit) — the same words, counted for it.
              ...(opening ? {
                trip: opening.trip,
                proactive: {
                  trigger: opening.trigger, ...(opening.variant ? { variant: opening.variant } : {}),
                  ...(opening.lang ? { lang: opening.lang } : {}),
                  // The owner's contact test: the side /offer named, and the field the yes opens at once.
                  ...(opening.contactTest ? { contact_test: opening.contactTest } : {}),
                  ...(opening.confirm?.field ? { field: opening.confirm.field } : {}),
                },
              } : {}),
            }),
          })
          if (initRes.ok) {
            const initData = await initRes.json()
            saveVisitorToken(initData.visitor_token)
            const newConvId = initData.conversation_id as string
            if (opening) {
              // The opening is stored now. Read messages after it (before the stream connects, so the
              // first poll does not draw it twice), and show the words as stored, after the server's
              // output guard. Not stored (the visitor's open chat came back, or the feature was switched
              // off meanwhile): the conversation does not have it, so the visitor does not see it either.
              if (typeof initData.opening_at === 'string') lastMsgTime.current = initData.opening_at
              const stored = typeof initData.opening === 'string' ? initData.opening : null
              setMessages(prev => stored
                ? prev.map(m => (m.id === openingId(opening) ? { ...m, content: stored } : m))
                : prev.filter(m => m.id !== openingId(opening)))
              markProactiveConversation(newConvId)
              // Only the anonymous visitor id, as after the form: the client's details never go to PostHog.
              const vid = getVisitorId()
              if (vid) posthog?.identify(vid)
            }
            if (hello) {
              // Held: the hello is the chat's first message, stored; messages are read after it. Not held (it ran
              // out, the agent no longer takes chats): the chat is an ordinary one that never had it, so it leaves
              // the screen too, as an opening not stored does.
              const held = typeof initData.live_agent === 'string'
              if (held && typeof initData.live_at === 'string') lastMsgTime.current = initData.live_at
              if (!held) setMessages(prev => prev.filter(m => m.id !== liveId(hello)))
              // No form behind it: this visitor's chat on the next pages, as the card's.
              markProactiveConversation(newConvId)
              const vid = getVisitorId()
              if (vid) posthog?.identify(vid)
              onLiveSending?.(false)
              onLiveReplied?.(held)
            }
            activeConvId = newConvId
            setConvId(newConvId)
            apiFetch(`${apiUrl}/api/chat/session/${newConvId}/open`, { method: 'POST' }).catch(() => {})
            try { localStorage.setItem('bbc_conv_id', newConvId) } catch {}
            try { localStorage.setItem('bbc_conv_ts', Date.now().toString()) } catch {}
            // Wait for SSE useEffect to fire and connect
            await new Promise<void>((resolve) => {
              const check = setInterval(() => {
                clearInterval(check)
                resolve()
              }, 400)
            })
          } else {
            // Not created: the next send carries the opening (or the hello's tap) again.
            if (opening) openingRef.current = opening
            if (hello) {
              liveRef.current = hello
              onLiveSending?.(false)
            }
          }
        } catch (e) {
          if (opening) openingRef.current = opening
          if (hello) {
            liveRef.current = hello
            onLiveSending?.(false)
          }
          console.warn(`[bbc-widget] /init failed${opening || hello ? '' : ', falling back to direct POST'}`, e)
        }
        // The card's chat exists only through /chat/init (its opening, the page's trip, the label), and so does
        // the agent's (the tap): the direct POST would make a plain one the widget can't find again, or one that
        // isn't the agent's. The send fails instead, and Retry carries the opening or the tap again.
        if ((opening || hello) && !activeConvId) throw new Error('init failed')
      }

      const res = await apiFetch(`${apiUrl}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: text,
          conversation_id: activeConvId || convId,
          tunnel,
          visitor: {
            name: visitor.name || null,
            email: visitor.email || null,
            phone: visitor.phone || null,
            country_code: visitor.country_code || null,
          },
          metadata: metadata || {},
          visitor_id: getVisitorId(),
          // A button the server answers without the model (the card's yes, a channel, "I'll wait here").
          ...(opts?.button ? { button: opts.button } : {}),
          ...(opts?.priceSeen ? { price_seen: opts.priceSeen } : {}),
        }),
      })

      if (!res.ok) throw new Error('API error')
      const data = await res.json()
      const conversationState = isExistingConversation ? 'existing' : 'new'
      posthog?.capture('chat_message_sent', {
        tunnel,
        conversation_state: conversationState,
      })
      posthog?.logger.info('chat_message_delivered', {
        tunnel,
        conversation_state: conversationState,
      })

      // The 200 IS the save receipt: promote the optimistic bubble to
      // `sent-*` so no poll can ever drop it. When a batch later carries
      // the real user row, reconcileBatch swaps it in (content match).
      setMessages(prev => promoteTemp(prev, optimisticMsg.id, Date.now()))
      // An email typed into the chat is a contact left too (the model's turn keeps it): no second chance for the row
      // beside a refused form. Only the visit's mark: "before you go" is still the server's to skip.
      if (!opts?.button && typedEmail(text)) markContactLeft()

      if (data.conversation_id && !convId) {
        setConvId(data.conversation_id)
        apiFetch(`${apiUrl}/api/chat/session/${data.conversation_id}/open`, { method: 'POST' }).catch(() => {})
        try { localStorage.setItem('bbc_conv_id', data.conversation_id) } catch {}
        // Save initial timestamp for 30-minute rolling expiry
        try { localStorage.setItem('bbc_conv_ts', Date.now().toString()) } catch {}
      }

      // Whether this send's reply is still streaming in; any other answer
      // ends its turn (no reply will stream for it).
      let streams = false
      // Handle system_messages from routing response (zero polling duplicates)
      if (data.system_messages && data.system_messages.length > 0) {
        setMessages(prev => [
          // Remove optimistic connecting message
          ...prev.filter(m => m.id !== '__connecting_temp__'),
          // Add real system messages with real UUIDs from backend
          ...data.system_messages.map((sm: any) => ({
            id: sm.id,
            role: 'system' as const,
            content: sm.content,
            created_at: sm.created_at,
          })),
        ])
        // Show quick reply buttons only on new conversations
        if (data.quick_replies && data.quick_replies.length > 0 && !savedConvId) {
          setQuickReplies(data.quick_replies)
        }
        // Update lastMsgTime so next poll doesn't re-fetch user message
        const lastSysTs = data.system_messages[data.system_messages.length - 1]?.created_at
        if (lastSysTs) lastMsgTime.current = lastSysTs
      } else if (data.streaming) {
        // The answer shown at once was not the server's after all (the model answers this one): it goes, and the
        // field it opened with it.
        if (localId) {
          setMessages(prev => prev.filter(m => m.id !== localId))
          setQuickReplies(null)
          if (opts?.local?.field) closeAutoField(localId)
        }
        // Only while this send's reply is still on its way. When its
        // stream_end came in first, marking it streaming here left the typing
        // dots under the finished reply until the next send (17 Nov 2026).
        streams = Boolean(activeConvId || convId) && turns.isOpen(seq)
        if (streams) {
          setIsStreaming(true)
          isStreamingRef.current = true
        }
      } else if (data.message && data.type !== 'queued') {
        // A stored answer names its row: the poll then never draws it twice. The one shown at once (A2)
        // takes the stored words and id; a row the poll already brought stays alone.
        const storedId = typeof data.message_id === 'string' ? data.message_id : null
        const storedAt = typeof data.created_at === 'string' ? data.created_at : null
        const aiMsg: Message = {
          id: storedId || `ai-${Date.now()}`,
          role: 'ai',
          content: data.message,
          created_at: storedAt || new Date().toISOString(),
        }
        setMessages(prev => {
          const already = storedId ? prev.some(m => m.id === storedId) : false
          if (localId) {
            return already ? prev.filter(m => m.id !== localId) : prev.map(m => (m.id === localId ? aiMsg : m))
          }
          return already ? prev : [...prev, aiMsg]
        })
        const buttons = readButtons(data.buttons)
        if (buttons.length) setQuickReplies(buttons.map(b => ({ label: b.label, kind: b.kind })))
        else if (!localId && opts?.button) setQuickReplies(null)
        // A channel's contact the server kept. One it could not read is asked for again, that channel's button first
        // (card_buttons._unreadable): nothing was given, and "before you go" may still come.
        const channel = opts?.button === 'sms' || opts?.button === 'email' ? opts.button : null
        if (channel && !buttons.some(b => b.kind === channel)) contactGiven()
        if (storedAt) {
          if (!lastMsgTime.current || storedAt > lastMsgTime.current) lastMsgTime.current = storedAt
        } else {
          lastMsgTime.current = new Date(Date.now() + 2000).toISOString()
        }
      }
      if (!streams) turns.over(seq)
      // Extend 30-minute rolling session on every successful message
      try { localStorage.setItem('bbc_conv_ts', Date.now().toString()) } catch {}
    } catch {
      posthog?.capture('chat_message_send_failed', { tunnel })
      posthog?.logger.warn('chat_message_delivery_failed', { tunnel })
      turns.over(seq)
      // The answer shown at once was never stored: it goes with the failed send (Retry brings both back), and the
      // field it opened with it.
      if (localId) {
        setMessages(prev => prev.filter(m => m.id !== localId))
        setQuickReplies(null)
        if (opts?.local?.field) closeAutoField(localId)
      }
      // A later send may already be streaming its own reply: leave it be.
      if (turns.isLatest(seq)) {
        setStreamingText('')
        setIsStreaming(false)
        isStreamingRef.current = false
      }
      // Clean up optimistic message if the request failed
      setMessages(prev => prev.filter(m => m.id !== '__connecting_temp__'))
      const errMsg: Message = {
        id: `err-${Date.now()}`,
        role: 'system',
        content: `Something went wrong. Please try again or call ${brand.contactPhone} for immediate help.`,
        created_at: new Date().toISOString(),
      }
      setMessages(prev => [...prev, errMsg])
    } finally {
      if (localId) setLocalPending(false)
      // Only the latest send unlocks: an earlier one answering late would
      // drop the dots while the next reply is still coming.
      if (!isStreamingRef.current && turns.isLatest(seq)) {
        setSending(false)
      }
    }
  }

  const handleSend = () => {
    const text = input.trim()
    if (!text) return
    atBottomRef.current = true
    if (openingRef.current) {
      // Typed instead of tapped: the card's buttons have done their job.
      onProactiveEngaged?.(text)
      setQuickReplies(null)
    }
    if (sending) {
      // The assistant is still answering: one message at a time reaches the server, so this one waits
      // its turn on screen and goes out by itself when the answer is in.
      setQueued(q => [...q, { id: `queued-${Date.now()}`, text }])
      sendTypingEvent('')
    } else {
      sendMessage(text)
    }
    setQuickReplies(null)
    setInput('')
    requestAnimationFrame(() => {
      const el = composerRef.current
      if (el) el.style.height = ''
    })
  }

  // Send what waited, in order, as soon as the assistant has finished.
  useEffect(() => {
    if (sending || queued.length === 0) return
    const [next, ...rest] = queued
    setQueued(rest)
    sendMessage(next.text, next.opts)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sending, queued])

  /** A send that failed: take it and its error note away, and send the text again (as the button it was). */
  const retry = (msg: Message) => {
    setMessages(prev => prev.filter(m => m.id !== msg.id && !m.id.startsWith('err-')))
    atBottomRef.current = true
    if (sending) setQueued(q => [...q, { id: `queued-${Date.now()}`, text: msg.content, opts: msg.sendOpts }])
    else sendMessage(msg.content, msg.sendOpts)
  }

  /** A send now, or its turn in the line while an answer is on its way. */
  const sendOrQueue = (text: string, opts?: SendOptions) => {
    atBottomRef.current = true
    if (sending) setQueued(q => [...q, { id: `queued-${Date.now()}`, text, opts }])
    else sendMessage(text, opts)
  }

  const handleQuickReply = (chip: Chip) => {
    const text = chip.label
    posthog?.capture('chat_quick_reply_selected', { tunnel, ...(chip.kind ? { kind: chip.kind } : {}) })
    setQuickReplies(null)
    if (chip.kind === 'sms' || chip.kind === 'email') {
      // Where the agent writes: the field opens, nothing is sent before the contact is in it.
      openChannel(chip.kind)
      return
    }
    if (openingRef.current && isNotNow(text)) {
      // "Not now" never reaches the server: the panel closes and the card stays away for 24 h.
      onProactiveDismiss?.('not_now')
      onClose()
      return
    }
    if (openingRef.current) onProactiveEngaged?.(text)
    if (chip.kind === 'wait') return sendOrQueue(text, { button: 'wait' })
    const yes = yesOptions(openingRef.current, text)
    if (yes) return sendMessage(text, yes)
    sendMessage(text)
  }

  // ── Where the agent writes (channel.ts): the client's mobile number (SMS) or email ─────────────────
  /** `auto`: opened by itself with the yes's answer (the owner's contact test's field side), not by a tap on a
   *  channel; the field then offers the other channel itself. `owner`: the send whose answer opened it (the switch
   *  to the other channel keeps it); a channel tapped by the visitor belongs to no send. */
  const openChannel = (kind: ChannelKind, auto = false, owner?: string | null) => {
    setChannelForm(kind)
    setChannelValue('')
    setChannelError('')
    setAutoField(auto)
    if (!auto) autoFieldOwnerRef.current = null
    else if (owner !== undefined) autoFieldOwnerRef.current = owner
    posthog?.capture('chat_channel_opened', { channel: kind, tunnel, ...(auto ? { auto: true } : {}), ...fieldTestRef.current })
    // The field takes the chips' place and more: the question it answers stays in view above it.
    requestAnimationFrame(() => {
      scrollToEnd(false)
      channelInputRef.current?.focus({ preventScroll: true })
    })
  }
  const closeChannel = () => {
    setChannelForm(null)
    setChannelError('')
    setAutoField(false)
    autoFieldOwnerRef.current = null
    // Back to the choice: the buttons the field opened over when it opened by itself (the contact test's field
    // side keeps the answer's buttons under it), else the channels again (the answer they were offered under).
    setQuickReplies(q => (q && q.length ? q : [
      { label: w.sentSms, kind: 'sms' },
      { label: w.sentEmail, kind: 'email' },
    ]))
  }
  /** The field opened by itself goes, with no choice put back: the answer it came with was not the server's. Only
   *  while it is still the field `owner`'s answer opened; Back, or a channel the visitor tapped since, made it theirs. */
  const closeAutoField = (owner: string | null) => {
    if (!owner || autoFieldOwnerRef.current !== owner) return
    autoFieldOwnerRef.current = null
    setChannelForm(null)
    setChannelError('')
    setAutoField(false)
  }
  const submitChannel = (e: Event) => {
    e.preventDefault()
    const kind = channelForm
    if (!kind) return
    const price = channelPrice.trim().slice(0, 40)
    let contact: string | null
    if (kind === 'sms') {
      const e164 = validPhone(COUNTRIES, channelValue, country)
      contact = e164 ? readablePhone(COUNTRIES, e164) : null
      if (!contact) return setChannelError(w.badPhone)
    } else {
      contact = validEmail(channelValue)
      if (!contact) return setChannelError(w.badEmail)
    }
    const text = channelMessage(widgetLang(), kind, contact, beatRef.current ? price : '')
    setChannelForm(null)
    setChannelValue('')
    setChannelPrice('')
    setChannelError('')
    setAutoField(false)
    autoFieldOwnerRef.current = null
    if (openingRef.current) onProactiveEngaged?.(text)
    // Only the kind goes to PostHog, never the contact.
    posthog?.capture('chat_channel_given', { channel: kind, tunnel, price: Boolean(beatRef.current && price), ...(autoField ? { auto: true } : {}), ...fieldTestRef.current })
    sendOrQueue(text, { button: kind, ...(beatRef.current && price ? { priceSeen: price } : {}) })
  }

  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() }
  }

  // ── Presentation ─────────────────────────────────────────────────────────
  // Only rows with an id and some text are drawn (a defence in depth: nothing
  // malformed from the server may ever break the render).
  const shown = messages.filter(m =>
    m && typeof m.id === 'string' && typeof m.content === 'string' && m.content.trim() !== '')
  const speaker = (m: Message) =>
    m.role === 'user' ? 'out' : m.role === 'agent' ? `agent:${(m.agent_name || '').trim()}` : m.role
  const timeOf = (iso: string) => {
    try { return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) } catch { return '' }
  }
  const startsGroup = (i: number) => {
    if (i === 0) return true
    const prev = shown[i - 1]
    const cur = shown[i]
    if (speaker(prev) !== speaker(cur)) return true
    return new Date(cur.created_at).getTime() - new Date(prev.created_at).getTime() > 5 * 60_000
  }
  const endsGroup = (i: number) => i === shown.length - 1 || startsGroup(i + 1)
  // Who answers now (owner, 17 Nov 2026): once a person writes in the chat, the header shows their
  // photo and name instead of Alex's, and Alex again if the chat comes back to him.
  const operator = currentOperator(shown)
  // Alex's avatar: his photo (PersonaAvatar.tsx).
  const brandAvatar = <PersonaAvatar cls="bw-ava bw-ava--persona" />
  // The answer already on screen (A2) has no "is typing" under it while its send finishes.
  const aiWaiting = ((sending || isStreaming) && !streamingText && !localPending) || (starting && shown.length === 0)
  // Day dividers, only for a conversation that is not all from today (a restored one).
  const dayKey = (iso: string) => { const d = new Date(iso); return isNaN(d.getTime()) ? '' : d.toDateString() }
  const today = new Date().toDateString()
  const showDays = shown.some(m => dayKey(m.created_at) && dayKey(m.created_at) !== today)
  const dayLabel = (iso: string) => {
    const d = new Date(iso)
    const yesterday = new Date(Date.now() - 86_400_000).toDateString()
    if (d.toDateString() === today) return 'Today'
    if (d.toDateString() === yesterday) return 'Yesterday'
    return d.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' })
  }
  const dots = <span class="bw-dots" aria-hidden="true"><i /><i /><i /></span>

  return (
    <div
      class={`bw-panel${embedded ? ' bw-panel--embedded' : ''}${swapped ? ' bw-panel--swap' : ''}${large && !embedded ? ' bw-panel--large' : ''}${visible ? '' : ' bw-panel--hidden'}`}
      aria-hidden={visible ? undefined : 'true'}
      role="dialog"
      aria-modal={isPhone && !embedded ? 'true' : 'false'}
      aria-labelledby="bw-chat-title"
    >
      <PanelTop
        titleId="bw-chat-title"
        operator={operator}
        closeLabel="Close chat"
        canEnlarge={!embedded}
        onClose={embedded ? undefined : () => {
          if (openingRef.current && !convId) onProactiveDismiss?.('close')
          // The agent's hello, unanswered: the visitor's "not now" to them (nobody says hello for an hour).
          if (liveRef.current && !convId) onLiveDismiss?.()
          notifySessionClose('minimized', true)
          onClose()
        }}
      />

      <div class="bw-logwrap">
      <div ref={logRef} class="bw-log" role="log" aria-live="polite" aria-label="Conversation" onScroll={onLogScroll}>
        {shown.map((msg, i) => {
          const newDay = showDays && (i === 0 || dayKey(shown[i - 1].created_at) !== dayKey(msg.created_at))
          const day = newDay && dayKey(msg.created_at)
            ? <div key={`day-${msg.id}`} class="bw-day">{dayLabel(msg.created_at)}</div>
            : null
          if (msg.role === 'system') {
            return [day,
              <div key={msg.id} class={`bw-note${msg.id.startsWith('err-') ? ' bw-note--error' : ''}`}>
                {richText(msg.content)}
              </div>,
            ]
          }
          const out = msg.role === 'user'
          const first = startsGroup(i)
          const last = endsGroup(i)
          const isOperator = msg.role === 'agent'
          const operatorName = (msg.agent_name || '').trim() || CONSULTANT_LABEL
          return [day,
            <div key={msg.id} class={`bw-row ${out ? 'bw-row--out' : 'bw-row--in'}${first || newDay ? ' bw-row--first' : ''}`}>
              {!out && (first || newDay) && (
                <div class="bw-who">
                  {isOperator
                    ? (msg.agent_avatar_url && !brokenAvatars[msg.id]
                        ? <img
                            class="bw-ava"
                            src={msg.agent_avatar_url}
                            alt=""
                            width={28}
                            height={28}
                            onError={() => setBrokenAvatars(prev => ({ ...prev, [msg.id]: true }))}
                          />
                        : <span class="bw-ava bw-ava--initials" aria-hidden="true">{agentInitials(operatorName)}</span>)
                    : brandAvatar}
                  <span>
                    <b>{isOperator ? operatorName : brand.persona.name}</b>
                    {isOperator && operatorName !== CONSULTANT_LABEL ? `, ${CONSULTANT_LABEL.toLowerCase()}` : ''}
                    {' · '}{timeOf(msg.created_at)}
                  </span>
                </div>
              )}
              <div class="bw-msg">{richText(msg.content)}</div>
              {out && last && (() => {
                // A send that failed shows the error note after it; say so here instead of "Sending".
                const failed = msg.id.startsWith('temp-') && shown.slice(i + 1).some(m => m.id.startsWith('err-'))
                if (failed) {
                  return (
                    <button type="button" class="bw-meta bw-meta--failed bw-retry" onClick={() => retry(msg)}>
                      Not sent. <u>Retry</u>
                    </button>
                  )
                }
                return <div class="bw-meta">{msg.id.startsWith('temp-') ? 'Sending' : timeOf(msg.created_at)}</div>
              })()}
            </div>,
          ]
        })}

        {streamingText && (
          <div class={`bw-row bw-row--in${shown.length && speaker(shown[shown.length - 1]) === 'ai' ? '' : ' bw-row--first'}`}>
            {!(shown.length && speaker(shown[shown.length - 1]) === 'ai') && (
              <div class="bw-who">{brandAvatar}<span><b>{brand.persona.name}</b></span></div>
            )}
            <div class="bw-msg">{streamingText}</div>
          </div>
        )}

        {aiWaiting && !agentTyping.is_typing && (
          <div class="bw-row bw-row--in bw-row--first">
            <div class="bw-who">{brandAvatar}<span><b>{brand.persona.name}</b> is typing</span></div>
            {dots}
            <span class="bw-sr">{`${brand.persona.name}, the AI ${brand.persona.role}, is typing`}</span>
          </div>
        )}

        {agentTyping.is_typing && (
          <div class="bw-row bw-row--in bw-row--first">
            <div class="bw-who">
              <span class="bw-ava bw-ava--initials" aria-hidden="true">{agentInitials(agentTyping.name || CONSULTANT_LABEL)}</span>
              <span><b>{agentTyping.name || CONSULTANT_LABEL}</b> is typing</span>
            </div>
            {dots}
          </div>
        )}

        {queued.map((q, i) => (
          <div key={q.id} class={`bw-row bw-row--out bw-row--queued${i === 0 ? ' bw-row--first' : ''}`}>
            <div class="bw-msg">{q.text}</div>
            {i === queued.length - 1 && <div class="bw-meta">Sends when the reply is in</div>}
          </div>
        ))}
      </div>
      {hasUnseen && (
        <button type="button" class="bw-unseen" onClick={() => scrollToEnd(true)}>
          New message <IconArrowDown />
        </button>
      )}
      </div>

      {/* Quick replies — shown after the welcome message, hidden after a tap */}
      {quickReplies && quickReplies.length > 0 && !channelForm && (
        <div class="bw-chips">
          {quickReplies.map((chip, i) => (
            <button
              key={i}
              type="button"
              class={`bw-chip${chip.kind === 'sms' || chip.kind === 'email' ? ' bw-chip--channel' : ''}`}
              onClick={() => handleQuickReply(chip)}
            >
              {(chip.kind === 'sms' || chip.kind === 'email') && <ChannelIcon kind={chip.kind} />}
              {chip.label}
            </button>
          ))}
        </div>
      )}

      {/* Where the agent writes: the client's mobile number (SMS) or email (channel.ts). */}
      {channelForm && (
        <form class="bw-channel" onSubmit={submitChannel} noValidate>
          <label class="bw-channel__label" for="bw-channel-input">
            <ChannelIcon kind={channelForm} />
            {channelForm === 'sms' ? w.phoneLabel : w.emailLabel}
          </label>
          <div class={`bw-channel__row${channelError ? ' bw-channel__row--error' : ''}`}>
            {channelForm === 'sms' && (
              <select
                class="bw-channel__cc"
                aria-label={w.country}
                value={country.code}
                onChange={e => {
                  const picked = COUNTRIES.find(c => c.code === (e.target as HTMLSelectElement).value)
                  if (picked) setCountry(picked)
                }}
              >
                {COUNTRIES.map(c => <option key={c.code} value={c.code}>{`${c.flag} ${c.dial}`}</option>)}
              </select>
            )}
            <input
              ref={channelInputRef}
              id="bw-channel-input"
              class="bw-channel__input"
              type={channelForm === 'sms' ? 'tel' : 'email'}
              inputMode={channelForm === 'sms' ? 'tel' : 'email'}
              autoComplete={channelForm === 'sms' ? 'tel' : 'email'}
              enterkeyhint="send"
              value={channelValue}
              aria-invalid={channelError ? 'true' : undefined}
              aria-describedby="bw-channel-hint"
              onInput={e => {
                setChannelValue((e.target as HTMLInputElement).value)
                lastActivityRef.current = Date.now()
                if (channelError) setChannelError('')
              }}
            />
          </div>
          {beatRef.current && (
            <input
              class="bw-channel__price"
              type="text"
              inputMode="decimal"
              maxLength={40}
              placeholder={w.priceLabel}
              aria-label={w.priceLabel}
              value={channelPrice}
              onInput={e => setChannelPrice((e.target as HTMLInputElement).value)}
            />
          )}
          {channelError
            ? <div class="bw-channel__error" id="bw-channel-hint" role="alert">{channelError}</div>
            : <div class="bw-channel__hint" id="bw-channel-hint">{channelForm === 'sms' ? w.phoneHint : w.emailHint}</div>}
          <div class="bw-channel__actions">
            {/* The field opened by itself (the contact test's field side): the other channel in one tap. */}
            {autoField && (
              <button
                type="button"
                class="bw-channel__other"
                onClick={() => openChannel(channelForm === 'sms' ? 'email' : 'sms', true)}
              >
                {channelForm === 'sms' ? w.otherEmail : w.otherSms}
              </button>
            )}
            <button type="button" class="bw-channel__back" onClick={closeChannel}>{w.back}</button>
            <button type="submit" class="bw-channel__send">{w.send}</button>
          </div>
        </form>
      )}

      <div class={`bw-compose${channelForm ? ' bw-compose--hidden' : ''}`}>
        <textarea
          ref={composerRef}
          id="bw-composer"
          class="bw-input"
          rows={1}
          value={input}
          enterkeyhint="send"
          placeholder="Write a message"
          aria-label="Type a message"
          onInput={e => {
            const val = (e.target as HTMLTextAreaElement).value
            lastActivityRef.current = Date.now()
            setInput(val)
            sendTypingEvent(val)
            fitComposer()
          }}
          onKeyDown={handleKeyDown}
          onFocus={keepComposerInView}
        />
        <button
          type="button"
          class="bw-send"
          onClick={handleSend}
          disabled={!input.trim()}
          aria-label={sending ? 'Send after the reply' : 'Send message'}
          title={sending ? 'Sends when the reply is in' : 'Send'}
        >
          <IconArrowUp />
        </button>
      </div>
    </div>
  )
}
