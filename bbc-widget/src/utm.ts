// bbc-widget/src/utm.ts — where the visitor came from: campaign tags, click ids, referrer (first campaign touch)

const UTM_KEY = 'bbc_utm'
const UTM_PARAMS = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content'] as const
// Platform click IDs (Google/Facebook/Microsoft auto-tagging)
const CLICK_IDS = ['gclid', 'fbclid', 'msclkid', 'ttclid', 'kayak_click_id', 'kclid'] as const

export type UtmFields = Partial<Record<(typeof UTM_PARAMS)[number], string>>

type Kept = Record<string, string>

/**
 * What THIS page load saw in the address bar. The tab's storage carries it from page to page; it is not the
 * only place it is kept. Between 10 Sep and 4 Nov 2026, 90 of the 725 chats started in Apple's browsers (Safari,
 * Chrome on iPhone, the Facebook and Instagram apps on iPhone) reached the server with nothing at all, not even
 * their page: when the form was sent, the tab's storage gave nothing back. In every other browser: 0 of 596.
 * Why it happens there is not known (a blocked storage, or one emptied after the capture). Either way, with
 * this copy what the page load saw still reaches the lead.
 */
let seen: Kept | null = null

function readStored(): Kept | null {
  try {
    const raw = sessionStorage.getItem(UTM_KEY)
    const kept = raw ? JSON.parse(raw) : null
    return kept && typeof kept === 'object' && !Array.isArray(kept) ? kept : null
  } catch {
    return null // storage blocked, or a value that is not ours
  }
}

/** A campaign tag or an ad click id: something that says where the visitor came from. */
function namesASource(kept: Kept | null): boolean {
  return !!kept && [...UTM_PARAMS, ...CLICK_IDS].some((k) => !!kept[k])
}

/**
 * Capture UTM params from the current URL (first campaign touch of the tab).
 * Call once at widget init — before render.
 *
 * First touch: what the tab already knows is kept. One exception: a tab whose first page named no source
 * (a visitor who typed the address, then came back through an ad in the same tab) takes the first page that
 * does. A page without tags never replaces one with tags.
 *
 * `attribution: false`: the site loaded the chat for a visitor who has not accepted marketing cookies (the script
 * tag's data-attribution="off"). Then nothing is kept, no campaign tags, click ids, referrer or Analytics id, and
 * anything an earlier page of the tab kept is dropped; the lead still names its page (getUtm).
 */
export function captureUtm({ attribution = true }: { attribution?: boolean } = {}): void {
  if (!attribution) {
    seen = null
    try { sessionStorage.removeItem(UTM_KEY) } catch { /* storage blocked: nothing was kept */ }
    return
  }
  const data: Kept = {}
  try {
    const params = new URLSearchParams(window.location.search)
    for (const k of UTM_PARAMS) {
      const v = params.get(k)
      if (v) data[k] = v
    }
    // Also capture page context
    const pageUrl = window.location.href.split('?')[0]
    if (pageUrl) data.page_url = pageUrl
    if (document.referrer) data.referrer = document.referrer

    for (const cid of CLICK_IDS) {
      const val = params.get(cid)
      if (val) data[cid] = val
    }

    // Google Analytics client ID from _ga cookie
    try {
      const gaCookie = document.cookie.split(';').map(c => c.trim()).find(c => c.startsWith('_ga='))
      if (gaCookie) {
        // _ga=GA1.2.XXXXXXXXXX.XXXXXXXXXX → extract client ID part
        const parts = gaCookie.split('=')[1]?.split('.')
        if (parts && parts.length >= 4) {
          data.google_analytics_client_id = parts.slice(2).join('.')
        }
      }
    } catch { /* cookie access blocked */ }
  } catch { /* SSR / iframe sandbox — ignore */ }

  if (Object.keys(data).length === 0) return
  seen = data
  try {
    const stored = readStored()
    if (!stored || (!namesASource(stored) && namesASource(data))) {
      sessionStorage.setItem(UTM_KEY, JSON.stringify(data))
    }
  } catch { /* storage blocked: `seen` still carries this page's tags to the lead */ }
}

/**
 * What the lead carries: the tab's first touch, else what this page load saw, else the page alone.
 */
export function getUtm(): UtmFields & {
  page_url?: string
  referrer?: string
  google_analytics_client_id?: string
  kayak_click_id?: string
  gclid?: string
  fbclid?: string
  msclkid?: string
  ttclid?: string
  kclid?: string
} {
  const stored = readStored()
  // The tab's record wins, unless it names no source and this page load saw one (the write was refused).
  if (stored && (namesASource(stored) || !namesASource(seen))) return stored
  if (seen) return seen
  // Nothing kept (attribution off, or nothing to capture): the page the chat is on, without its query.
  try {
    const page_url = window.location.href.split('?')[0]
    return page_url ? { page_url } : {}
  } catch {
    return {}
  }
}
