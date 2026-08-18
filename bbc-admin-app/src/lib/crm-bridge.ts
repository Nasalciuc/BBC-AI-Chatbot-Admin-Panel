/**
 * The panel tells the CRM that a conversation is waiting.
 *
 * The CRM embeds this panel in a permanently mounted, cross-origin iframe.
 * The agent hears our notification sound, but the panel stays minimised and
 * nothing else happens — so they hear a noise, do not know what it was, and
 * keep working the lead they already had. The conversation waits.
 *
 * The CRM cannot fix that alone: same-origin policy means it can read nothing
 * inside the frame — not the DOM, not a counter, not the fact that we rang.
 * From the outside an incoming conversation is invisible. `postMessage` is
 * the browser's sanctioned channel for exactly this, and the receiving half
 * (auto-open, desktop notification, badge, suppression after a manual
 * minimise) is already built and waiting on their side.
 *
 * This module is deliberately DEPENDENCY-FREE. Its one collaborator, the
 * origin validator, arrives through `installCrmBridge`, for two reasons: the
 * single origin allow-list stays in `crm-embed-auth.ts` and is never
 * duplicated, and this file stays importable by `node --test`, which cannot
 * resolve `@/` aliases or `import.meta.env`.
 */

/** Everything we are ever allowed to say. Note what is absent: no name, no
 *  email, no phone, no message text. A `preview` field would land in a
 *  desktop notification on a possibly shared screen, and a client's first
 *  sentence is usually exactly what should not be there. */
export type ChatBridgeMessage =
  | { type: 'chat:hello'; unread: number }
  | { type: 'chat:incoming'; unread: number; conversationId: string }
  | { type: 'chat:unread'; unread: number }
  | { type: 'chat:presence'; online: boolean; ready: boolean }

export type CrmBridgeDeps = {
  /** `isAllowedCrmOrigin` from crm-embed-auth — the ONE allow-list. */
  isAllowedOrigin: (origin: string) => boolean
}

const CHAT_SOURCE = 'bbc-chat'
const CRM_SOURCE = 'bbc-crm'

let deps: CrmBridgeDeps | null = null
/** Origin learned from the `crm:hello` handshake — the most trustworthy
 *  answer to "who is my parent", because they proved it by talking to us. */
let handshakeOrigin: string | null = null
/** Last `unread` we actually sent. At one cycle every 5s, without this the
 *  CRM would receive 720 identical messages an hour. */
let lastUnreadSent: number | null = null
let lastPresenceSent: string | null = null

/**
 * Conversations this agent has not dealt with yet — a LEVEL, not an event.
 *
 * The heartbeat's `needsAttention` set is rebuilt from scratch every cycle and
 * membership requires a *delta since the previous cycle*. Five seconds after a
 * conversation arrives its message count has not moved, so it silently leaves
 * that set even though nobody answered the client. Sending its size as
 * `unread` would give the CRM a badge that reads 1, then 0, while the client
 * is still waiting — and two clients waiting would read as 1, because only
 * the one that changed this tick is counted.
 *
 * So the level is accumulated here: things enter when the heartbeat notices
 * them or when the SERVER says the client is waiting for a first reply, and
 * they leave only when the agent actually opens them or the conversation is
 * gone from their list. `.size` of that is a queue depth.
 */
const waiting = new Set<string>()

export function isEmbedded(): boolean {
  return typeof window !== 'undefined' && window.parent !== window
}

/** Only ever called with an origin the allow-list has already accepted AND
 *  that is genuinely our parent — see the handler in `installCrmBridge`. */
export function rememberParentOrigin(origin: string): void {
  if (!deps || !deps.isAllowedOrigin(origin)) {
    console.warn('[crm-bridge] refusing to remember an origin that is not allow-listed')
    return
  }
  handshakeOrigin = origin
}

/**
 * Where to post. Never `'*'`: with a wildcard, any site that frames this
 * panel could read the agent's conversation state.
 *
 * Order: the handshake origin first; `document.referrer` second, and only if
 * it passes the same allow-list. If neither validates we send nothing and
 * throw nothing — a panel that cannot identify its parent stays quiet.
 */
function resolveTargetOrigin(): string | null {
  if (!deps) return null
  // Re-validated on every send: a stored value is not a trusted value, and
  // this costs one function call per five seconds.
  if (handshakeOrigin) {
    return deps.isAllowedOrigin(handshakeOrigin) ? handshakeOrigin : null
  }
  if (typeof document === 'undefined') return null
  const referrer = document.referrer
  if (!referrer) return null
  try {
    const origin = new URL(referrer).origin
    // Our OWN origin passes the allow-list too — the panel and the CRM share
    // a registrable domain — and after an in-frame navigation the referrer is
    // this panel, not the CRM. Posting there means posting to a
    // parent that is not us — every message silently dropped by the browser,
    // no error anywhere, feature dead while looking healthy.
    if (typeof window !== 'undefined' && origin === window.location?.origin) return null
    return deps.isAllowedOrigin(origin) ? origin : null
  } catch {
    // A referrer we cannot parse is not an origin we may post to.
    console.warn('[crm-bridge] unparseable document.referrer; not posting')
    return null
  }
}

export function sendToCrm(msg: ChatBridgeMessage): void {
  if (!isEmbedded()) return
  const targetOrigin = resolveTargetOrigin()
  if (!targetOrigin) return
  try {
    window.parent.postMessage({ source: CHAT_SOURCE, ...msg }, targetOrigin)
  } catch (e) {
    // Loud, not silent: a broken bridge must be visible in the console, and
    // it must never reach the heartbeat that called us.
    console.warn('[crm-bridge] postMessage failed:', e)
  }
}

/**
 * One attention cycle. Everything here is already computed by the heartbeat;
 * nothing is fetched, and no attention decision is made or changed.
 *
 * - `attentionIds`  — the set the panel is ringing about right now.
 * - `needsAgentIds` — conversations the SERVER has reserved for this agent
 *   with the client waiting for a first human reply (`status: 'needs_agent'`,
 *   flipped to `active` by the backend on the agent's first message). This is
 *   what survives a page reload and the heartbeat's baseline pass, both of
 *   which wipe the delta set.
 * - `viewingId`     — the conversation open on screen: handled, by definition.
 * - `liveIds`       — everything still in the agent's list; anything else was
 *   closed or re-assigned and must stop being counted.
 *
 * `baselineTaken === false` is the heartbeat's first pass after mount, where
 * everything already on screen is treated as seen so logging in never sets
 * the alert off. We honour it: no `chat:incoming` on that cycle, or the CRM
 * would jump in front of the agent on every reload. The count still goes.
 *
 * Only one `chat:incoming` per cycle even when two conversations arrive
 * together — otherwise the CRM takes the agent's screen twice in a row.
 */
export function reportAttentionCycle(opts: {
  attentionIds: string[]
  needsAgentIds: string[]
  liveIds: string[]
  viewingId: string | null
  newArrivalId: string | null
  baselineTaken: boolean
}): void {
  const { attentionIds, needsAgentIds, liveIds, viewingId, newArrivalId, baselineTaken } = opts
  if (!isEmbedded()) return

  for (const id of attentionIds) waiting.add(id)
  for (const id of needsAgentIds) waiting.add(id)
  if (viewingId) waiting.delete(viewingId)
  const live = new Set(liveIds)
  for (const id of [...waiting]) if (!live.has(id)) waiting.delete(id)

  const unread = waiting.size

  if (newArrivalId && baselineTaken) {
    lastUnreadSent = unread
    sendToCrm({ type: 'chat:incoming', unread, conversationId: newArrivalId })
    return
  }
  if (unread !== lastUnreadSent) {
    lastUnreadSent = unread
    sendToCrm({ type: 'chat:unread', unread })
  }
}

/** Presence, only when it actually changed. */
export function reportPresence(ready: boolean): void {
  if (!isEmbedded()) return
  const key = `1:${ready}`
  if (key === lastPresenceSent) return
  lastPresenceSent = key
  sendToCrm({ type: 'chat:presence', online: true, ready })
}

/**
 * Mount the CRM→panel listener. Returns its cleanup function.
 *
 * The handshake is the reason this exists at all: an agent who reloads the
 * CRM mid-shift, with conversations already waiting, would otherwise see a
 * badge of zero. A badge that lies exactly when it matters is worse than no
 * badge.
 */
export function installCrmBridge(d: CrmBridgeDeps): () => void {
  deps = d
  if (!isEmbedded()) {
    // Opened normally, in its own tab: nothing observable changes. No
    // listener, no message, not even a console line.
    return () => {
      deps = null
    }
  }

  const handler = (event: MessageEvent) => {
    if (!deps) return
    // A `message` event can be delivered by ANY window holding a handle to
    // this frame — a sibling iframe reached through window.top.frames, an
    // opener, a nested frame. Without this check a sibling on an allow-listed
    // origin could pin `handshakeOrigin` to itself, and from then on every
    // post to the real parent is dropped for origin mismatch: a permanent,
    // silent kill switch on the agent's alerts.
    if (event.source !== window.parent) return
    if (!deps.isAllowedOrigin(event.origin)) return
    const data = event.data
    if (!data || typeof data !== 'object') return
    if (data.source !== CRM_SOURCE) return

    if (data.type === 'crm:hello') {
      rememberParentOrigin(event.origin)
      const unread = waiting.size
      lastUnreadSent = unread
      sendToCrm({ type: 'chat:hello', unread })
    }
  }

  window.addEventListener('message', handler)
  return () => {
    window.removeEventListener('message', handler)
    deps = null
  }
}

/** Tests only. Production has exactly one bridge for the page's lifetime. */
export function __resetBridgeState(): void {
  deps = null
  handshakeOrigin = null
  lastUnreadSent = null
  lastPresenceSent = null
  waiting.clear()
}
