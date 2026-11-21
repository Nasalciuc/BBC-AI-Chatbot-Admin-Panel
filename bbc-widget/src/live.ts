/**
 * "On the site now" (contracts/live.md): from 20 s on the page, while the page is visible and there is no chat
 * yet, the widget beats to bbc-live every 15 s and waits for an agent's hello. The hello shows as a card of plain
 * text; a tap on it starts a chat that is already the agent's (/chat/init with the tap token inside the hello).
 *
 * Nothing the visitor typed goes in a beat: the path (its query and fragment cut), the page's trip as codes and
 * dates, where they came from, the screen. The API says whether this is on (GET /chat/live) and where bbc-live is.
 */

import { useEffect, useRef, useState } from 'preact/hooks'
import { apiFetch, getVisitorToken } from './api'
import { beatBody, nextPollDelay, readConfig, readHello, type LiveConfig, type LiveHello } from './liveRead'
import type { TripPayload } from './tripContext'
import { getUtm } from './utm'

export type { LiveHello } from './liveRead'

const AFTER_S = 20
const BEAT_S = 15

function visible(): boolean {
  try { return document.visibilityState !== 'hidden' } catch { return true }
}

function isPhone(): boolean {
  try { return window.matchMedia('(max-width: 767px)').matches } catch { return false }
}

function liveFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const token = getVisitorToken()
  const headers = new Headers(init.headers)
  if (token) headers.set('X-Visitor-Token', token)
  return fetch(url, { ...init, headers, credentials: 'omit', mode: 'cors' })
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

    const beat = () => {
      const cfg = configRef.current
      if (stopped || !cfg?.enabled || !cfg.url) return
      if (visible()) {
        let path = '/'
        try { path = location.pathname } catch { /* keep "/" */ }
        const body = beatBody({ path, trip: tripRef.current(), utm: getUtm() as Record<string, string | undefined>,
          phone: isPhone(), site, chatOpen: chatOpenRef.current, hasChat: false })
        liveFetch(`${cfg.url}/v1/beat`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body) }).catch(() => { /* a missed beat: the next one counts */ })
      }
      later(beat, (cfg.beat_s || BEAT_S) * 1000)
    }

    let failures = 0
    const poll = async () => {
      const cfg = configRef.current
      if (stopped || !cfg?.enabled || !cfg.hello || !cfg.url) return
      if (!visible()) { later(poll, 5_000); return }
      abort = new AbortController()
      const timeout = setTimeout(() => abort?.abort(), 35_000)
      let status: number | 'error' = 'error'
      try {
        const res = await liveFetch(`${cfg.url}/v1/inbox`, { signal: abort.signal })
        status = res.status
        if (res.status === 200) {
          const h = readHello(await res.json(), Date.now())
          if (h && !stopped) { setHello(h); return }
        }
        if (res.status === 401 || res.status === 503) { stopped = true; return }
      } catch { /* aborted or offline: try again below */ } finally {
        clearTimeout(timeout)
      }
      failures = status === 204 || status === 200 ? 0 : failures + 1
      if (!stopped) later(poll, nextPollDelay(status, failures))
    }

    later(() => {
      if (stopped) return
      apiFetch(`${apiUrl}/api/chat/live`)
        .then(r => (r.ok ? r.json() : null))
        .then(raw => {
          configRef.current = readConfig(raw)
          if (stopped || !configRef.current.enabled) return
          beat()
          void poll()
        })
        .catch(() => { /* the API can't say: no live visitor this page */ })
    }, AFTER_S * 1000)

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
      if (cfg?.url) liveFetch(`${cfg.url}/v1/dismiss`, { method: 'POST' }).catch(() => { /* runs out in 15 min */ })
    },
    taken: () => { doneRef.current = true; setHello(null) },
  }
}
