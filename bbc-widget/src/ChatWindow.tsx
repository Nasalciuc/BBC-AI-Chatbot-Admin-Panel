import { useState, useEffect, useRef } from 'preact/hooks'
import { shouldSendTyping, sseRetryDelay, AGENT_TYPING_EXPIRE_MS } from './realtime-policy'
import { ACTIVITY_EVENTS, BEAT_MS, idleSeconds, isMobileDevice, shouldBeat, signalQuery } from './presence-policy'
import type { PresenceSignal } from './presence-policy'
import { apiFetch, ensureVisitorToken, getVisitorId, getVisitorToken, saveVisitorToken } from './api'
import { getTurnstileToken } from './turnstile'
import type { FormCheck } from './TunnelForm'
import brand from './config'
import { promoteTemp, reconcileBatch } from './reconcile'
import { PanelTop } from './PanelTop'
import { IconArrowDown, IconArrowUp } from './icons'
import { richText } from './richText'
import { useIsPhone } from './phoneSheet'
import { posthog } from './posthog'

interface Message {
  id: string
  role: 'user' | 'ai' | 'agent' | 'system'
  content: string
  created_at: string
  /** Operator identity — present on role='agent' messages only. */
  agent_name?: string | null
  agent_avatar_url?: string | null
}

const CONSULTANT_LABEL = 'Consultant'

function agentInitials(name: string): string {
  return name
    .split(' ')
    .map(w => w[0] || '')
    .join('')
    .toUpperCase()
    .slice(0, 2)
}

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

export function ChatWindow({
  tunnel, visitor, metadata, formCheck, onClose, apiUrl, embedded = false, swapped = false,
  visible = true, draft = '', onUnread,
}: Props) {
  const visibleRef = useRef(visible)
  visibleRef.current = visible
  const mountedAtRef = useRef(Date.now())
  const isPhone = useIsPhone()
  const savedConvId = getCachedConvId()

  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState<string>(() => ssGet(DRAFT_KEY) ?? (savedConvId ? '' : draft))
  const [sending, setSending] = useState(false)
  const [streamingText, setStreamingText] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const isStreamingRef = useRef(false)
  const [quickReplies, setQuickReplies] = useState<string[] | null>(null)
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
  /** Messages written while the assistant was still answering: they go out, in order, as soon as it finishes. */
  const [queued, setQueued] = useState<{ id: string; text: string }[]>(() => {
    try { return JSON.parse(ssGet(QUEUE_KEY) || '[]') } catch { return [] }
  })
  /** The greeting is on its way (fresh sessions): the log shows the assistant typing instead of nothing. */
  const [starting, setStarting] = useState(!savedConvId)
  /** The visitor scrolled up to read: new messages no longer pull the log down; a button offers to. */
  const atBottomRef = useRef(true)
  const [hasUnseen, setHasUnseen] = useState(false)
  /** Operator photos that failed to load → show initials instead of a broken image. */
  const [brokenAvatars, setBrokenAvatars] = useState<Record<string, boolean>>({})

  // Safety net: force-unlock sending after 15s (protects against any stuck state)
  useEffect(() => {
    if (!sending) return
    const timeout = setTimeout(() => {
      setSending(false)
      setIsStreaming(false)
      isStreamingRef.current = false
    }, 15000)
    return () => clearTimeout(timeout)
  }, [sending])

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
  const scrollToEnd = (smooth = true) => {
    const el = logRef.current
    if (!el) return
    atBottomRef.current = true
    setHasUnseen(false)
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
  }
  const onLogScroll = () => {
    const el = logRef.current
    if (!el) return
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 72
    atBottomRef.current = near
    if (near) setHasUnseen(false)
  }
  useEffect(() => {
    if (atBottomRef.current) scrollToEnd(messages.length > 1)
    else setHasUnseen(true)
  }, [messages.length, streamingText, sending, agentTyping.is_typing, queued.length])

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
      : 'Assistant'
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

  // The composer takes focus on computers; on touch screens the keyboard waits for a tap.
  const focusComposer = () => {
    try {
      if (!window.matchMedia('(pointer: fine)').matches) return
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
          metadata: metadata || {},
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
    if (savedConvId) return
    startConversation()
  // eslint-disable-next-line
  }, [])

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
            if (prev.some(m => m.id === msg.id)) return prev
            return [...prev, msg]
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

  const sendMessage = async (text: string) => {
    if (!text.trim()) return
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
    }
    setMessages(prev => [...prev, optimisticMsg])

    try {
      // Sprint 2: Pre-create conv for SSE streaming on first message
      let activeConvId = convId
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
              metadata: metadata || {},
              visitor_id: getVisitorId(),
              turnstile_token,
              form_check: formCheck,
            }),
          })
          if (initRes.ok) {
            const initData = await initRes.json()
            saveVisitorToken(initData.visitor_token)
            const newConvId = initData.conversation_id as string
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
          }
        } catch (e) {
          console.warn('[bbc-widget] /init failed, falling back to direct POST', e)
        }
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

      if (data.conversation_id && !convId) {
        setConvId(data.conversation_id)
        apiFetch(`${apiUrl}/api/chat/session/${data.conversation_id}/open`, { method: 'POST' }).catch(() => {})
        try { localStorage.setItem('bbc_conv_id', data.conversation_id) } catch {}
        // Save initial timestamp for 30-minute rolling expiry
        try { localStorage.setItem('bbc_conv_ts', Date.now().toString()) } catch {}
      }

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
        if (activeConvId || convId) {
          setIsStreaming(true)
          isStreamingRef.current = true
        }
      } else if (data.message && data.type !== 'queued') {
        const aiMsg: Message = {
          id: `ai-${Date.now()}`,
          role: 'ai',
          content: data.message,
          created_at: new Date().toISOString(),
        }
        setMessages(prev => [...prev, aiMsg])
        lastMsgTime.current = new Date(Date.now() + 2000).toISOString()
      }
      // Extend 30-minute rolling session on every successful message
      try { localStorage.setItem('bbc_conv_ts', Date.now().toString()) } catch {}
    } catch {
      posthog?.capture('chat_message_send_failed', { tunnel })
      posthog?.logger.warn('chat_message_delivery_failed', { tunnel })
      setStreamingText('')
      setIsStreaming(false)
      isStreamingRef.current = false
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
      if (!isStreamingRef.current) {
        setSending(false)
      }
    }
  }

  const handleSend = () => {
    const text = input.trim()
    if (!text) return
    atBottomRef.current = true
    if (sending) {
      // The assistant is still answering: one message at a time reaches the server, so this one waits
      // its turn on screen and goes out by itself when the answer is in.
      setQueued(q => [...q, { id: `queued-${Date.now()}`, text }])
      sendTypingEvent('')
    } else {
      sendMessage(text)
    }
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
    sendMessage(next.text)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sending, queued])

  /** A send that failed: take it and its error note away, and send the text again. */
  const retry = (msg: Message) => {
    setMessages(prev => prev.filter(m => m.id !== msg.id && !m.id.startsWith('err-')))
    atBottomRef.current = true
    if (sending) setQueued(q => [...q, { id: `queued-${Date.now()}`, text: msg.content }])
    else sendMessage(msg.content)
  }

  const handleQuickReply = (text: string) => {
    posthog?.capture('chat_quick_reply_selected', { tunnel })
    setQuickReplies(null)
    sendMessage(text)
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
  const lastAgent = [...shown].reverse().find(m => m.role === 'agent')
  const lastAgentName = (lastAgent?.agent_name || '').trim()
  const subtitle = lastAgentName ? `Chatting with ${lastAgentName}` : brand.tagline
  const brandAvatar = <img class="bw-ava bw-ava--brand" src={brand.logoUrl} alt="" width={28} height={28} />
  const aiWaiting = ((sending || isStreaming) && !streamingText) || (starting && shown.length === 0)
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
      class={`bw-panel${embedded ? ' bw-panel--embedded' : ''}${swapped ? ' bw-panel--swap' : ''}${visible ? '' : ' bw-panel--hidden'}`}
      aria-hidden={visible ? undefined : 'true'}
      role="dialog"
      aria-modal={isPhone && !embedded ? 'true' : 'false'}
      aria-labelledby="bw-chat-title"
    >
      <PanelTop
        titleId="bw-chat-title"
        subtitle={subtitle}
        closeLabel="Close chat"
        onClose={embedded ? undefined : () => { notifySessionClose('minimized', true); onClose() }}
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
                    <b>{isOperator ? operatorName : 'Assistant'}</b>
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
              <div class="bw-who">{brandAvatar}<span><b>Assistant</b></span></div>
            )}
            <div class="bw-msg">{streamingText}</div>
          </div>
        )}

        {aiWaiting && !agentTyping.is_typing && (
          <div class="bw-row bw-row--in bw-row--first">
            <div class="bw-who">{brandAvatar}<span><b>Assistant</b> is typing</span></div>
            {dots}
            <span class="bw-sr">The assistant is typing</span>
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
      {quickReplies && quickReplies.length > 0 && (
        <div class="bw-chips">
          {quickReplies.map((text, i) => (
            <button key={i} type="button" class="bw-chip" onClick={() => handleQuickReply(text)}>
              {text}
            </button>
          ))}
        </div>
      )}

      <div class="bw-compose">
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
