/**
 * "On the site now" (contracts/live.md): from 20 s on the page, while the page is visible and there is no chat
 * yet, the widget beats to bbc-live every 15 s and waits for an agent's hello. The panel lists the visitors Alex's
 * card didn't start a chat with (the beat says what came of it: agentTest.ts). The hello shows as Alex's card does
 * (the owner, 23 Nov 2026): in the chat when it is open, else as the card beside the button on a phone, else the
 * chat opens with it on a computer (Widget.tsx). The visitor's answer starts a chat that is already the agent's
 * (/chat/init with the tap token inside the hello). The hello is kept for the visit's next pages until it runs out.
 *
 * Nothing the visitor typed goes in a beat: the path (its query and fragment cut), the page's trip as codes and
 * dates, where they came from, the screen. The API says whether this is on and where bbc-live is, and hands the
 * visitor's pass to it (POST /chat/live): the visitor id's first pass needs no proof, the next ones the last
 * one, which this keeps in localStorage. The chat's own visitor token is not used: most visitors have none.
 */

import { useEffect, useRef, useState } from 'preact/hooks'
import { alexAt, alexCard, alexShown, noteSide } from './agentTest'
import { apiFetch, ensureVisitorId } from './api'
import {
  afterRefusedPass, beatBody, failedAsk, keptPass, nextPollDelay, readConfig, readHello, readKeptHello, renewalOf,
  type BeatAlexCard, type LiveConfig, type LiveHello, type Renewal,
} from './liveRead'
import { posthog } from './posthog'
import { activityLine, siteEvents, sitePage } from './siteSignals'
import type { TripPayload } from './tripContext'
import { getUtm } from './utm'

export type { LiveHello } from './liveRead'

const AFTER_S = 20
const BEAT_S = 15
const PASS_KEY = 'bbc_live_pass'
// sessionStorage: the hello not answered yet, for the visit's next pages (bbc-live hands it over once).
const HELLO_KEY = 'bbc_live_hello'
/** A pass bbc-live refuses (401) is renewed at most this often. */
const RENEW_MS = 5 * 60_000

function visible(): boolean {
  try { return document.visibilityState !== 'hidden' } catch { return true }
}

/** A phone's screen, as the widget's other parts read it: the hello is a card there, the chat elsewhere. */
export function isPhone(): boolean {
  try { return window.matchMedia('(max-width: 767px)').matches } catch { return false }
}

/** This visitor has a chat (started on this page or another): no beat after it, no hello into it. */
function hasConversation(): boolean {
  try { return !!localStorage.getItem('bbc_conv_id') } catch { return false }
}

/** The hello kept for this visit, while it holds. */
export function keptHello(): LiveHello | null {
  try { return readKeptHello(sessionStorage.getItem(HELLO_KEY), Date.now()) } catch { return null }
}

function keepHello(h: LiveHello | null): void {
  try {
    if (h) sessionStorage.setItem(HELLO_KEY, JSON.stringify(h))
    else sessionStorage.removeItem(HELLO_KEY)
  } catch { /* this page holds it all the same */ }
}

/** What came of Alex's card, as the beat says it: how long ago by this browser's clock (agentTest.ts). */
function alexCardNow(nowMs: number): BeatAlexCard | null {
  const c = alexCard()
  return c ? { state: c.state, secondsAgo: (nowMs - c.at) / 1000, trigger: c.trigger } : null
}

function storedPass(): string | null {
  try { return keptPass(localStorage.getItem(PASS_KEY)) } catch { return null }
}

function keepPass(pass: string): void {
  try { localStorage.setItem(PASS_KEY, pass) } catch { /* this page beats with it all the same */ }
}

function liveFetch(url: string, pass: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  headers.set('X-Live-Pass', pass)
  return fetch(url, { ...init, headers, credentials: 'omit', mode: 'cors' })
}

/** POST /chat/live: on or off, where, and a pass (kept for the next page). */
async function askConfig(apiUrl: string): Promise<LiveConfig> {
  const visitorId = ensureVisitorId()
  const res = await apiFetch(`${apiUrl}/api/chat/live`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ visitor_id: visitorId, live_pass: storedPass() }),
  })
  const cfg = res.ok ? readConfig(await res.json()) : failedAsk(res.status)
  // The owner's agent-first test: an answer names this visitor's side (Alex's side gets no pass, never listed).
  if (res.ok) noteSide(cfg.test)
  // Kept only for the id it was asked for: a visitor id changed meanwhile (a form for someone else) must not
  // inherit the last one's pass, which would only be refused (CodeRabbit #273).
  if (cfg.enabled && cfg.pass && ensureVisitorId() === visitorId) keepPass(cfg.pass)
  return cfg
}

/**
 * The hook: `active` while the visitor has no chat (the chat closed, or open on an opening nobody answered). It
 * returns the hello waiting for them (or null), `dismiss` ("not now", sent to bbc-live) and `taken` (answered:
 * the chat is made with its tap, stop).
 */
export function useLive({ apiUrl, active, chatOpen, trip, site }: {
  apiUrl: string; active: boolean; chatOpen: boolean; trip: () => TripPayload | null; site: string
}): { hello: LiveHello | null; dismiss: () => void; taken: () => void } {
  // A hello an earlier page received and nobody answered yet (bbc-live hands it over once); none over a chat.
  const [hello, setHelloState] = useState<LiveHello | null>(() => {
    const kept = keptHello()
    if (kept && hasConversation()) {
      keepHello(null)
      return null
    }
    return kept
  })
  const setHello = (h: LiveHello | null) => {
    keepHello(h)
    setHelloState(h)
  }
  const configRef = useRef<LiveConfig | null>(null)
  const doneRef = useRef(false)
  const chatOpenRef = useRef(chatOpen)
  chatOpenRef.current = chatOpen
  const tripRef = useRef(trip)
  tripRef.current = trip

  useEffect(() => {
    if (!active || doneRef.current) return
    let stopped = false
    const timers: ReturnType<typeof setTimeout>[] = []
    let abort: AbortController | null = null
    const later = (fn: () => void, ms: number) => { timers.push(setTimeout(fn, ms)) }

    // A pass bbc-live refuses (run out, the secret changed) is asked for again, at most every RENEW_MS; the beat
    // and the poll refused together share one renewal.
    let renewedAt = 0
    let renewing: Promise<Renewal> | null = null
    const renew = (): Promise<Renewal> => {
      if (renewing) return renewing
      if (Date.now() - renewedAt < RENEW_MS) return Promise.resolve('later')
      renewedAt = Date.now()
      renewing = askConfig(apiUrl)
        .then((cfg): Renewal => {
          if (stopped) return 'later'
          // Not for now: the config in hand stays, so the beats go on and the next renewal can bring a pass
          // (CodeRabbit #273). Off or a new pass replaces it.
          if (cfg.later) return 'later'
          configRef.current = cfg
          return renewalOf(cfg)
        })
        .catch((): Renewal => 'later')
        .finally(() => { renewing = null })
      return renewing
    }

    const sendBeat = (cfg: LiveConfig, chat: boolean) => {
      if (!cfg.url || !cfg.pass) return
      let path = '/'
      try { path = location.pathname } catch { /* keep "/" */ }
      // What the visitor is doing right now, for the agent who says hello first (the owner, 22 Nov 2026): the
      // kind of page and the steps on the site, as the site told the widget; never what they typed.
      const body = beatBody({ path, trip: tripRef.current(), utm: getUtm() as Record<string, string | undefined>,
        phone: isPhone(), site, chatOpen: chatOpenRef.current, hasChat: chat,
        pageType: sitePage()?.pageType ?? null, activity: activityLine(siteEvents()),
        // The agent-first test: when Alex's card may come, so the agent sees how long they have.
        alexAt: alexAt(),
        // What came of Alex's card: the panel lists the visitors it didn't start a chat with.
        alexCard: alexCardNow(Date.now()) })
      liveFetch(`${cfg.url}/v1/beat`, cfg.pass, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body) })
        .then(res => { if (res.status === 401 && !chat) void renew() })
        .catch(() => { /* a missed beat: the next one counts */ })
    }

    // The chat started (Alex's answered, or the agent's): one last beat says so, and the panel stops listing the
    // visitor at once instead of a minute later; then the beats end, and the poll with them.
    const lastBeat = () => {
      const cfg = configRef.current
      stopped = true
      abort?.abort()
      if (cfg?.enabled) sendBeat(cfg, true)
    }

    const beat = () => {
      const cfg = configRef.current
      if (stopped || !cfg?.enabled || !cfg.url || !cfg.pass) return
      if (hasConversation()) return lastBeat()
      if (visible()) sendBeat(cfg, false)
      later(beat, (cfg.beat_s || BEAT_S) * 1000)
    }

    let failures = 0
    const poll = async () => {
      const cfg = configRef.current
      if (stopped || !cfg?.enabled || !cfg.hello || !cfg.url || !cfg.pass) return
      if (!visible()) { later(poll, 5_000); return }
      abort = new AbortController()
      const timeout = setTimeout(() => abort?.abort(), 35_000)
      let status: number | 'error' = 'error'
      try {
        const res = await liveFetch(`${cfg.url}/v1/inbox`, cfg.pass, { signal: abort.signal })
        status = res.status
        if (res.status === 200) {
          const h = readHello(await res.json(), Date.now())
          // A chat that started meanwhile is the visitor's answer already: no hello over it.
          if (h && !stopped && !hasConversation()) {
            setHello(h)
            // The first step for the agent's words, as proactive_shown is for Alex's; after_alex: Alex's card showed
            // on this visit first, so this hello is a second word. `where`: in the open chat, the card beside the
            // button (a phone), or the chat it opens (a computer).
            posthog?.capture('live_hello_shown', {
              site, after_alex: alexShown(), where: chatOpenRef.current ? 'chat' : isPhone() ? 'card' : 'opens_chat',
              ...(alexCard() ? { alex_card: alexCard()?.state } : {}),
            })
            return
          }
        }
        if (res.status === 401) {
          // Renewed since this poll began (by the beat): wait with the new pass. A renewal that failed for now
          // (offline, the API down, asked too recently) tries again after RENEW_MS; only a refusal stops.
          const fresh = configRef.current?.pass !== cfg.pass
          const next = afterRefusedPass(fresh, fresh ? 'later' : await renew())
          if (next === 'poll') later(poll, 0)
          else if (next === 'wait') later(poll, RENEW_MS)
          else stopped = true
          return
        }
        if (res.status === 503) { stopped = true; return }
      } catch { /* aborted or offline: try again below */ } finally {
        clearTimeout(timeout)
      }
      failures = status === 204 || status === 200 ? 0 : failures + 1
      if (!stopped) later(poll, nextPollDelay(status, failures))
    }

    // The first config, from AFTER_S on the page; asked again after RENEW_MS while the API can't say for now.
    const start = () => {
      if (stopped) return
      askConfig(apiUrl)
        .then(cfg => {
          // An answer for an effect cleaned up meanwhile is not this page's config any more (CodeRabbit #273).
          if (stopped) return
          if (cfg.later) { later(start, RENEW_MS); return }
          configRef.current = cfg
          if (!cfg.enabled) return
          beat()
          void poll()
        })
        .catch(() => { if (!stopped) later(start, RENEW_MS) })
    }
    later(start, AFTER_S * 1000)

    return () => {
      // The widget stops beating because the chat started (Widget.tsx: no longer `active`): the last beat says so,
      // as the loop's own would have.
      if (!stopped && hasConversation()) lastBeat()
      stopped = true
      abort?.abort()
      timers.forEach(clearTimeout)
    }
  }, [active, apiUrl])

  // A hello that runs out goes away by itself, from this page and the next.
  useEffect(() => {
    if (!hello) return
    const t = setTimeout(() => setHello(null), Math.max(0, hello.expiresAt - Date.now()))
    return () => clearTimeout(t)
  }, [hello])

  return {
    hello,
    dismiss: () => {
      const cfg = configRef.current
      doneRef.current = true
      setHello(null)
      if (cfg?.url && cfg.pass) {
        liveFetch(`${cfg.url}/v1/dismiss`, cfg.pass, { method: 'POST' }).catch(() => { /* runs out in 15 min */ })
      }
    },
    taken: () => { doneRef.current = true; setHello(null) },
  }
}
