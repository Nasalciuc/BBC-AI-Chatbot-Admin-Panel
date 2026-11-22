/**
 * "On the site now" (contracts/live.md): from 20 s on the page, while the page is visible and there is no chat
 * yet, the widget beats to bbc-live every 15 s and waits for an agent's hello. The hello shows as a card of plain
 * text; a tap on it starts a chat that is already the agent's (/chat/init with the tap token inside the hello).
 *
 * Nothing the visitor typed goes in a beat: the path (its query and fragment cut), the page's trip as codes and
 * dates, where they came from, the screen. The API says whether this is on and where bbc-live is, and hands the
 * visitor's pass to it (POST /chat/live): the visitor id's first pass needs no proof, the next ones the last
 * one, which this keeps in localStorage. The chat's own visitor token is not used: most visitors have none.
 */

import { useEffect, useRef, useState } from 'preact/hooks'
import { apiFetch, ensureVisitorId } from './api'
import {
  afterRefusedPass, beatBody, failedAsk, keptPass, nextPollDelay, readConfig, readHello, renewalOf, type LiveConfig,
  type LiveHello, type Renewal,
} from './liveRead'
import { activityLine, siteEvents, sitePage } from './siteSignals'
import type { TripPayload } from './tripContext'
import { getUtm } from './utm'

export type { LiveHello } from './liveRead'

const AFTER_S = 20
const BEAT_S = 15
const PASS_KEY = 'bbc_live_pass'
/** A pass bbc-live refuses (401) is renewed at most this often. */
const RENEW_MS = 5 * 60_000

function visible(): boolean {
  try { return document.visibilityState !== 'hidden' } catch { return true }
}

function isPhone(): boolean {
  try { return window.matchMedia('(max-width: 767px)').matches } catch { return false }
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
  // Kept only for the id it was asked for: a visitor id changed meanwhile (a form for someone else) must not
  // inherit the last one's pass, which would only be refused (CodeRabbit #273).
  if (cfg.enabled && cfg.pass && ensureVisitorId() === visitorId) keepPass(cfg.pass)
  return cfg
}

/**
 * The hook: `active` while the visitor has no chat and the panel is closed. It returns the hello waiting for
 * them (or null), `dismiss` ("not now", sent to bbc-live) and `taken` (the visitor tapped it: stop).
 */
export function useLive({ apiUrl, active, chatOpen, trip, site }: {
  apiUrl: string; active: boolean; chatOpen: boolean; trip: () => TripPayload | null; site: string
}): { hello: LiveHello | null; dismiss: () => void; taken: () => void } {
  const [hello, setHello] = useState<LiveHello | null>(null)
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

    const beat = () => {
      const cfg = configRef.current
      if (stopped || !cfg?.enabled || !cfg.url || !cfg.pass) return
      if (visible()) {
        let path = '/'
        try { path = location.pathname } catch { /* keep "/" */ }
        // What the visitor is doing right now, for the agent who says hello first (the owner, 22 Nov 2026): the
        // kind of page and the steps on the site, as the site told the widget; never what they typed.
        const body = beatBody({ path, trip: tripRef.current(), utm: getUtm() as Record<string, string | undefined>,
          phone: isPhone(), site, chatOpen: chatOpenRef.current, hasChat: false,
          pageType: sitePage()?.pageType ?? null, activity: activityLine(siteEvents()) })
        liveFetch(`${cfg.url}/v1/beat`, cfg.pass, { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body) })
          .then(res => { if (res.status === 401) void renew() })
          .catch(() => { /* a missed beat: the next one counts */ })
      }
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
          if (h && !stopped) { setHello(h); return }
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
      stopped = true
      abort?.abort()
      timers.forEach(clearTimeout)
    }
  }, [active, apiUrl])

  // A hello that runs out goes away by itself.
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
