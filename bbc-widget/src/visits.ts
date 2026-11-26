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

import { askConfig, currentConfig, isPhone, pageConfig } from './live'
import { currentTrip } from './proactive'
import { posthogSessionId } from './posthog'
import { activityLine, siteEvents, sitePage } from './siteSignals'
import { getUtm } from './utm'
import { noteVisit } from './visitCount'
import { offCapture, onCapture } from './visitTap'
import {
  MAX_STEPS, addEvent, afterSent, cardAfter, cardConfirmed, cardStillOn, cardToKeep, eventToken, keptCardFor,
  newPageId, readSentMark, snapshotTimer, visitBody, whenRegistryOn, type SentMark, type VisitCard,
} from './visitsRead'

const CARD_KEY = 'bbc_visit_card'
/** The snapshots bbc-live took from this browser, any tab (visitsRead.ts SentMark): which visit a card is of. */
const SENT_KEY = 'bbc_visit_sent'
const EVERY_MS = 60_000
const AFTER_EVENT_MS = 1_500
const MAX_SENDS = 60
/** A pass bbc-live refuses (401) is asked for again at most this often. */
const RENEW_MS = 5 * 60_000

/** The running registry's stop, while one runs on this page. */
let stopCurrent: (() => void) | null = null

function ssGet(k: string): string | null { try { return sessionStorage.getItem(k) } catch { return null } }
function ssSet(k: string, v: string): void { try { sessionStorage.setItem(k, v) } catch { /* this page holds it */ } }
function lsGet(k: string): string | null { try { return localStorage.getItem(k) } catch { return null } }
function lsSet(k: string, v: string): void { try { localStorage.setItem(k, v) } catch { /* this tab's card rules */ } }
function sentMark(): SentMark { return readSentMark(lsGet(SENT_KEY)) }
/** A snapshot bbc-live answered (a 2xx): only one it took keeps its visit going. A beacon is only queued by the
 *  browser, never answered, and may never arrive: it marks nothing (CodeRabbit #282, round 3). */
function markSent(sentAt: number): void {
  const mark = sentMark()
  const next = afterSent(mark, sentAt)
  if (next !== mark) lsSet(SENT_KEY, JSON.stringify(next))
}

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

/** entry.tsx, once a page (never in the CRM's embedded chat): the page's snapshots. Returns the stop, which
 *  __BBC_WIDGET_TEARDOWN__ calls: its timers, listeners and the capture tap go with the widget (CodeRabbit #282,
 *  round 3). A second start returns the running one's stop. */
export function startVisits({ apiUrl, site }: { apiUrl: string; site: string }): () => void {
  if (typeof window === 'undefined') return () => {}
  if (stopCurrent) return stopCurrent
  const pageId = newPageId()
  const pageStart = Date.now()
  let seenMs = 0
  let seenFrom: number | null = visible() ? pageStart : null
  let events: string[] = []
  // The tab's card, while the registry's visit it belongs to goes on (cardStillOn).
  let card: VisitCard | null = keptCardFor(ssGet(CARD_KEY), sentMark(), Date.now())
  let sends = 0
  let seq = 0
  let everyTimer: ReturnType<typeof setTimeout> | null = null
  let renewedAt = 0
  let stopped = false

  // The browser's visit goes on with each snapshot and each event (visitCount.ts), the live list on or off. A card
  // whose registry visit went quiet, or ended while another tab began the next one, is not the next visit's: its
  // own first card is the card (CodeRabbit #282, round 3).
  const goingOn = () => {
    noteVisit()
    if (!card || cardStillOn(card, sentMark(), Date.now())) return
    card = null
    ssSet(CARD_KEY, '')
  }

  const seen = (now: number) => seenMs + (seenFrom === null ? 0 : now - seenFrom)

  // bbc-live answered a snapshot that carried `carried`: the browser's mark moves, and the card it carried is that
  // visit's from now on (cardStillOn).
  const answered = (carried: VisitCard | null, sentAt: number) => {
    markSent(sentAt)
    if (stopped) return
    const next = cardConfirmed(card, carried, sentAt)
    if (next === card) return
    card = next
    ssSet(CARD_KEY, next ? cardToKeep(next) : '')
  }

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
      conversationId: conversationId(), nowMs: now, pass, seq: ++seq,
    })
  }

  // The page's last snapshot (hidden, or left): sendBeacon when it can, else a fetch that outlives the page.
  const sendLast = () => {
    const cfg = currentConfig()
    if (stopped || !cfg?.visits || !cfg.url || !cfg.pass || sends >= MAX_SENDS + 5) return
    sends++
    goingOn()
    const carried = card
    const text = JSON.stringify(body(cfg.pass))
    const sentAt = Date.now()
    // A beacon the browser queued is not one bbc-live took: it marks nothing.
    try {
      if (navigator.sendBeacon?.(`${cfg.url}/v1/visit`, new Blob([text], { type: 'text/plain;charset=UTF-8' }))) return
    } catch { /* the fetch below */ }
    fetch(`${cfg.url}/v1/visit`, { method: 'POST', body: text, keepalive: true, credentials: 'omit', mode: 'cors',
      headers: { 'Content-Type': 'text/plain;charset=UTF-8' } }).then((res) => {
      if (res.ok) answered(carried, sentAt)
    }, () => { /* the visit keeps what it had */ })
  }

  const send = () => {
    const cfg = currentConfig()
    if (stopped || !cfg?.visits || !cfg.url || !cfg.pass || sends >= MAX_SENDS) return
    sends++
    goingOn()
    const carried = card
    const sentAt = Date.now()
    fetch(`${cfg.url}/v1/visit`, {
      method: 'POST', body: JSON.stringify(body(cfg.pass)), keepalive: true, credentials: 'omit', mode: 'cors',
      headers: { 'Content-Type': 'text/plain;charset=UTF-8' },
    }).then((res) => {
      // Only a snapshot bbc-live took keeps its visit going here: offline or refused, it ends there all the same.
      if (res.ok) answered(carried, sentAt)
      // A pass bbc-live refuses (run out, the secret changed): a new one, asked for, at most every RENEW_MS. Not
      // pageConfig: it keeps the page's first answer, the refused pass with it (CodeRabbit #282, round 3).
      if (res.status === 401 && Date.now() - renewedAt > RENEW_MS) {
        renewedAt = Date.now()
        askConfig(apiUrl).catch(() => { /* the next page asks again */ })
      }
    }, () => { /* offline or bbc-live away: the next snapshot says it all again */ })
  }

  // One snapshot waits at a time: the minute's, sent now, takes the one an event scheduled with it.
  const snap = snapshotTimer(send)

  const tap = (event: string, props: Record<string, unknown> | undefined) => {
    const now = Date.now()
    const t = eventToken(event, props)
    if (t) {
      events = addEvent(events, t)
      goingOn()
    }
    const next = cardAfter(card, event, props, now)
    if (next !== card) {
      card = next
      ssSet(CARD_KEY, next ? cardToKeep(next) : '')
    }
    if (t) snap.soon(AFTER_EVENT_MS)
  }
  onCapture(tap)

  // Every minute while the page is in sight, until the page's snapshots run out (then the loop stops too).
  const every = () => {
    everyTimer = null
    if (stopped) return
    if (visible()) snap.now()
    if (sends < MAX_SENDS) everyTimer = setTimeout(every, EVERY_MS)
  }

  const onVisibility = () => {
    const now = Date.now()
    if (visible()) {
      if (seenFrom === null) seenFrom = now
    } else {
      if (seenFrom !== null) seenMs += now - seenFrom
      seenFrom = null
      sendLast()
    }
  }
  const onPageHide = () => {
    if (seenFrom !== null) seenMs += Date.now() - seenFrom
    seenFrom = null
    sendLast()
  }
  try {
    document.addEventListener('visibilitychange', onVisibility)
    window.addEventListener('pagehide', onPageHide)
  } catch { /* no events: the minute's snapshots still go */ }

  let config: { cancel: () => void } | null = null
  const stop = () => {
    if (stopped) return
    stopped = true
    config?.cancel()
    snap.cancel()
    if (everyTimer !== null) clearTimeout(everyTimer)
    everyTimer = null
    try {
      document.removeEventListener('visibilitychange', onVisibility)
      window.removeEventListener('pagehide', onPageHide)
    } catch { /* none were added */ }
    offCapture(tap)
    if (stopCurrent === stop) stopCurrent = null
  }
  stopCurrent = stop

  // The page's config, now (the live list takes the same answer 20 s in): on with `visits`, the first snapshot and
  // the minute's loop. An answer "for now" or none at all is asked again, the live list on or off (whenRegistryOn;
  // CodeRabbit #282, round 4).
  config = whenRegistryOn(() => pageConfig(apiUrl), () => {
    if (stopped) return
    snap.now()
    everyTimer = setTimeout(every, EVERY_MS)
  })
  return stop
}

/** Tests only. */
export function resetVisitsForTests(): void {
  stopCurrent?.()
  stopCurrent = null
  onCapture(null)
}
