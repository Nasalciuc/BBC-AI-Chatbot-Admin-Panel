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
import { noteFormLeftArm } from './formLeftTest'
import { apiFetch, ensureVisitorId, getVisitorId } from './api'
import {
  afterRefusedPass, beatBody, configFor, failedAsk, keptPass, lastBeatAgain, nextPollDelay, readConfig, readHello,
  readKeptHello, renewalOf,
  type BeatAlexCard, type LiveConfig, type LiveHello, type Renewal,
} from './liveRead'
import { posthog } from './posthog'
import { activityLine, siteEvents, sitePage } from './siteSignals'
import type { TripPayload } from './tripContext'
import { getUtm } from './utm'
import { isReturning, noteVisit, timeZone } from './visitCount'

export type { LiveHello } from './liveRead'

const AFTER_S = 20
const BEAT_S = 15
const PASS_KEY = 'bbc_live_pass'
// sessionStorage: the hello not answered yet, for the visit's next pages (bbc-live hands it over once), and where
// bbc-live is: a hello kept from an earlier page can be dismissed before this page's config comes (20 s in).
const HELLO_KEY = 'bbc_live_hello'
const URL_KEY = 'bbc_live_url'
/** bbc-live counts one beat per 5 s (MinBeatGap); the last beat that didn't count (refused inside the gap, lost on
 *  the way, bbc-live down) goes once more after it. */
const LAST_BEAT_AGAIN_MS = 6_000
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

/** Where bbc-live is, as the last config on this visit said. */
function keptUrl(): string | null {
  try {
    const u = sessionStorage.getItem(URL_KEY)
    return u && /^https?:\/\/[^\s/]+/.test(u) ? u : null
  } catch { return null }
}

function keepUrl(u: string): void {
  try { sessionStorage.setItem(URL_KEY, u) } catch { /* this page's config holds it */ }
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

/** The last config the API gave this page (the registry reads its place and pass at each snapshot: visits.ts), for
 *  the visitor it was asked for only (configFor). */
let latest: { cfg: LiveConfig; visitorId: string } | null = null
export function currentConfig(): LiveConfig | null { return configFor(latest, getVisitorId()) }

let pageAsk: Promise<LiveConfig> | null = null
/** This page's config, asked once: at the page's start for the visit registry (visits.ts), the live list takes the
 *  same answer 20 s in. Asked again only after an answer "for now" or a failure (and by a pass renewal). */
export function pageConfig(apiUrl: string): Promise<LiveConfig> {
  if (!pageAsk) {
    pageAsk = askConfig(apiUrl).then(
      (cfg) => { if (cfg.later) pageAsk = null; return cfg },
      (err: unknown) => { pageAsk = null; throw err },
    )
  }
  return pageAsk
}

/** POST /chat/live: on or off, where, and a pass (kept for the next page). */
export async function askConfig(apiUrl: string): Promise<LiveConfig> {
  const visitorId = ensureVisitorId()
  const res = await apiFetch(`${apiUrl}/api/chat/live`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ visitor_id: visitorId, live_pass: storedPass() }),
  })
  const cfg = res.ok ? readConfig(await res.json()) : failedAsk(res.status)
  // The owner's agent-first test: an answer names this visitor's side (Alex's side gets no pass, never listed).
  if (res.ok) noteSide(cfg.test)
  // Test 1: the arm this answer names (none: the test is off), for the visit's events and its form_left moment. Only
  // for the visitor it was asked for: an id changed meanwhile (a form for someone else) has an arm of its own, and the
  // last one's would go on its events (CodeRabbit #291; Widget.tsx forgets it at the change). A browser that keeps no
  // visitor id (a new one at each ask: storage refused) gets no arm, on purpose: the test's unit is the visitor, and
  // an id only this page knows would put the same person in an arm anew, with no 24 h cap, on every page.
  if (res.ok && ensureVisitorId() === visitorId) noteFormLeftArm(cfg.form_left)
  // Kept only for the id it was asked for: a visitor id changed meanwhile (a form for someone else) must not
  // inherit the last one's pass, which would only be refused (CodeRabbit #273). The registry's pass alone (the list
  // off) is kept too: the next page renews it.
  if (cfg.pass && ensureVisitorId() === visitorId) keepPass(cfg.pass)
  if (cfg.url) keepUrl(cfg.url)
  if (!cfg.later && ensureVisitorId() === visitorId) latest = { cfg, visitorId }
  return cfg
}

/**
 * The hook: `active` while the visitor has no chat (the chat closed, or open on an opening nobody answered). It
 * returns the hello waiting for them (or null), `dismiss` ("not now", sent to bbc-live) and `taken` (answered:
 * the chat is made with its tap, stop).
 */
export function useLive({ apiUrl, active, chatOpen, trip, site }: {
  apiUrl: string; active: boolean; chatOpen: boolean; trip: () => TripPayload | null; site: string
}): {
  hello: LiveHello | null; dismiss: () => boolean; taken: () => void; sending: (on: boolean) => void
} {
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
  // A first message on its way carries the hello's tap (ChatWindow): until it lands the hello is the visitor's
  // answer, never a "not now" (Escape on the chat meanwhile: CodeRabbit #278).
  const tapInFlightRef = useRef(false)
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

    const sendBeat = (cfg: LiveConfig, chat: boolean, again = false) => {
      if (!cfg.url || !cfg.pass) return
      let path = '/'
      try { path = location.pathname } catch { /* keep "/" */ }
      // A beat keeps this visit going in the browser's count of visits (visitCount.ts).
      noteVisit()
      // What the visitor is doing right now, for the agent who says hello first (the owner, 22 Nov 2026): the
      // kind of page and the steps on the site, as the site told the widget; never what they typed.
      const body = beatBody({ path, trip: tripRef.current(), utm: getUtm() as Record<string, string | undefined>,
        phone: isPhone(), site, chatOpen: chatOpenRef.current, hasChat: chat,
        pageType: sitePage()?.pageType ?? null, activity: activityLine(siteEvents()),
        // The agent-first test: when Alex's card may come, so the agent sees how long they have.
        alexAt: alexAt(),
        // What came of Alex's card: the panel lists the visitors it didn't start a chat with.
        alexCard: alexCardNow(Date.now()),
        // The panel's groups (the owner, 25 Nov 2026): Australia and New Zealand by the time zone, and a browser
        // that was here before.
        tz: timeZone(), returning: isReturning() })
      liveFetch(`${cfg.url}/v1/beat`, cfg.pass, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body) })
        .then((res): number | 'error' => res.status, (): number | 'error' => 'error')
        .then(status => {
          if (status === 401 && !chat) void renew()
          // The last beat didn't count (202 inside bbc-live's 5 s gap, a request that failed, bbc-live down): once
          // more after the gap, or the panel would list the visitor until the visit runs out (CodeRabbit #278). Only
          // once. Not one of the effect's timers: it outlives the cleanup that sent it.
          if (chat && !again && lastBeatAgain(status)) setTimeout(() => sendBeat(cfg, true, true), LAST_BEAT_AGAIN_MS)
        })
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

    // The first config, from AFTER_S on the page (the answer the registry asked for at the page's start, when there
    // is one); asked again after RENEW_MS while the API can't say for now.
    const start = () => {
      if (stopped) return
      pageConfig(apiUrl)
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
    // "Not now" to the hello: false while a first message carries its tap (nothing is dismissed then). A hello kept
    // from an earlier page is dismissed before this page's config comes, with the pass and the place kept from it.
    dismiss: () => {
      if (tapInFlightRef.current) return false
      const cfg = configRef.current
      doneRef.current = true
      setHello(null)
      const url = cfg?.url || keptUrl()
      const pass = cfg?.pass || storedPass()
      if (url && pass) {
        liveFetch(`${url}/v1/dismiss`, pass, { method: 'POST' }).catch(() => { /* runs out in 15 min */ })
      }
      return true
    },
    taken: () => { doneRef.current = true; setHello(null) },
    sending: (on: boolean) => { tapInFlightRef.current = on },
  }
}
