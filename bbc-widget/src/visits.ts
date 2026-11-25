/**
 * The visit registry (contracts/live.md "The visit registry"; the owner, 25 Nov 2026: every visit kept, to judge
 * the card's words per group of visitors on everything that happened). From the page's start the widget sends
 * bbc-live a snapshot of the page: at once, a moment after anything the widget does on it (the events PostHog
 * gets, visitTap.ts), every minute while it is in sight, and when it is hidden or left. bbc-live merges a visitor's
 * snapshots into one visit; the API stores it once it has been quiet for 30 minutes.
 *
 * The API says whether the registry is on and hands the pass (POST /chat/live, `visits: true`); off, nothing is
 * sent. What a snapshot says is in visitsRead.ts: nothing the visitor typed. The pass rides in the body as plain
 * text: no preflight, and the page's last snapshot can go with sendBeacon, which sends no header.
 */

import { currentConfig, isPhone, pageConfig } from './live'
import { currentTrip } from './proactive'
import { posthogSessionId } from './posthog'
import { activityLine, siteEvents, sitePage } from './siteSignals'
import { getUtm } from './utm'
import { onCapture } from './visitTap'
import {
  MAX_STEPS, addEvent, cardAfter, eventToken, newPageId, readVisitCard, visitBody, type VisitCard,
} from './visitsRead'

const CARD_KEY = 'bbc_visit_card'
const EVERY_MS = 60_000
const AFTER_EVENT_MS = 1_500
const MAX_SENDS = 60
/** A pass bbc-live refuses (401) is asked for again at most this often. */
const RENEW_MS = 5 * 60_000

let started = false

function ssGet(k: string): string | null { try { return sessionStorage.getItem(k) } catch { return null } }
function ssSet(k: string, v: string): void { try { sessionStorage.setItem(k, v) } catch { /* this page holds it */ } }
function lsGet(k: string): string | null { try { return localStorage.getItem(k) } catch { return null } }

function visible(): boolean {
  try { return document.visibilityState !== 'hidden' } catch { return true }
}

/** The visitor's chat, when there is one (started on this page or another, by the form or by the card). */
function conversationId(): string | null {
  return lsGet('bbc_conv_id') || lsGet('bbc_conv_proactive')
}

function browserLang(): string | undefined {
  try { return navigator.language || undefined } catch { return undefined }
}

function timeZone(): string | undefined {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined } catch { return undefined }
}

/** entry.tsx, once a page (never in the CRM's embedded chat): the page's snapshots. */
export function startVisits({ apiUrl, site }: { apiUrl: string; site: string }): void {
  if (started || typeof window === 'undefined') return
  started = true
  const pageId = newPageId()
  const pageStart = Date.now()
  let seenMs = 0
  let seenFrom: number | null = visible() ? pageStart : null
  let events: string[] = []
  let card: VisitCard | null = readVisitCard(ssGet(CARD_KEY))
  let sends = 0
  let timer: ReturnType<typeof setTimeout> | null = null
  let renewedAt = 0

  const seen = (now: number) => seenMs + (seenFrom === null ? 0 : now - seenFrom)

  const body = (pass: string | undefined) => {
    const now = Date.now()
    let path = '/'
    let host = ''
    let referrer = ''
    try { path = location.pathname; host = location.host; referrer = document.referrer } catch { /* "/" */ }
    const utm = getUtm() as Record<string, string | undefined>
    return visitBody({
      pageId, path, pageType: sitePage()?.pageType ?? null, pageMs: now - pageStart, seenMs: seen(now),
      trip: currentTrip(), utm, referrer: utm.referrer || referrer, siteHost: host, phone: isPhone(), site,
      lang: browserLang(), tz: timeZone(), country: currentConfig()?.country, phSession: posthogSessionId(),
      events, steps: activityLine(siteEvents(), MAX_STEPS), card, hasChat: !!conversationId(),
      conversationId: conversationId(), nowMs: now, pass,
    })
  }

  // The page's last snapshot (hidden, or left): sendBeacon when it can, else a fetch that outlives the page.
  const sendLast = () => {
    const cfg = currentConfig()
    if (!cfg?.visits || !cfg.url || !cfg.pass || sends >= MAX_SENDS + 5) return
    sends++
    const text = JSON.stringify(body(cfg.pass))
    try {
      if (navigator.sendBeacon?.(`${cfg.url}/v1/visit`, new Blob([text], { type: 'text/plain;charset=UTF-8' }))) return
    } catch { /* the fetch below */ }
    fetch(`${cfg.url}/v1/visit`, { method: 'POST', body: text, keepalive: true, credentials: 'omit', mode: 'cors',
      headers: { 'Content-Type': 'text/plain;charset=UTF-8' } }).catch(() => { /* the visit keeps what it had */ })
  }

  const send = () => {
    timer = null
    const cfg = currentConfig()
    if (!cfg?.visits || !cfg.url || !cfg.pass || sends >= MAX_SENDS) return
    sends++
    fetch(`${cfg.url}/v1/visit`, {
      method: 'POST', body: JSON.stringify(body(cfg.pass)), keepalive: true, credentials: 'omit', mode: 'cors',
      headers: { 'Content-Type': 'text/plain;charset=UTF-8' },
    }).then((res) => {
      // A pass bbc-live refuses (run out, the secret changed): a new one, at most every RENEW_MS.
      if (res.status === 401 && Date.now() - renewedAt > RENEW_MS) {
        renewedAt = Date.now()
        pageConfig(apiUrl).catch(() => { /* the next page asks again */ })
      }
    }, () => { /* offline or bbc-live away: the next snapshot says it all again */ })
  }

  const soon = (ms: number) => {
    if (timer !== null) clearTimeout(timer)
    timer = setTimeout(send, ms)
  }

  onCapture((event, props) => {
    const now = Date.now()
    const t = eventToken(event, props)
    if (t) events = addEvent(events, t)
    const next = cardAfter(card, event, props, now)
    if (next !== card) {
      card = next
      ssSet(CARD_KEY, JSON.stringify(card))
    }
    if (t) soon(AFTER_EVENT_MS)
  })

  const every = () => {
    if (visible()) send()
    setTimeout(every, EVERY_MS)
  }

  try {
    document.addEventListener('visibilitychange', () => {
      const now = Date.now()
      if (visible()) {
        if (seenFrom === null) seenFrom = now
      } else {
        if (seenFrom !== null) seenMs += now - seenFrom
        seenFrom = null
        sendLast()
      }
    })
    window.addEventListener('pagehide', () => {
      if (seenFrom !== null) seenMs += Date.now() - seenFrom
      seenFrom = null
      sendLast()
    })
  } catch { /* no events: the minute's snapshots still go */ }

  // The page's config, now (the live list takes the same answer 20 s in): on with `visits`, the first snapshot.
  pageConfig(apiUrl).then((cfg) => {
    if (!cfg.visits) return
    send()
    setTimeout(every, EVERY_MS)
  }, () => { /* the API away: no registry on this page */ })
}

/** Tests only. */
export function resetVisitsForTests(): void {
  started = false
  onCapture(null)
}
