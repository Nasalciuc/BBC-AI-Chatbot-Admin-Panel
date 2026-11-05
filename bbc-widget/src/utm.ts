// bbc-widget/src/utm.ts — UTM capture from URL params (first-touch)

const UTM_KEY = 'bbc_utm'
const UTM_PARAMS = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content'] as const

export type UtmFields = Partial<Record<(typeof UTM_PARAMS)[number], string>>

/**
 * Capture UTM params from current URL into sessionStorage (first-touch).
 * Call once at widget init — before render.
 *
 * `attribution: false`: the site loaded the chat for a visitor who has not accepted marketing cookies (the script
 * tag's data-attribution="off"). Then nothing is kept, no campaign tags, click ids, referrer or Analytics id, and
 * anything an earlier page of the tab kept is dropped; the lead still names its page (getUtm).
 */
export function captureUtm({ attribution = true }: { attribution?: boolean } = {}): void {
  if (!attribution) {
    try { sessionStorage.removeItem(UTM_KEY) } catch { /* storage blocked: nothing was kept */ }
    return
  }
  try {
    const params = new URLSearchParams(window.location.search)
    const captured: UtmFields = {}
    for (const k of UTM_PARAMS) {
      const v = params.get(k)
      if (v) captured[k] = v
    }
    // Also capture page context
    const extra: Record<string, string> = {}
    const pageUrl = window.location.href.split('?')[0]
    if (pageUrl) extra.page_url = pageUrl
    if (document.referrer) extra.referrer = document.referrer

    // Platform click IDs (Google/Facebook/Microsoft auto-tagging)
    const clickIds = ['gclid', 'fbclid', 'msclkid', 'ttclid', 'kayak_click_id', 'kclid'] as const
    for (const cid of clickIds) {
      const val = params.get(cid)
      if (val) extra[cid] = val
    }

    // Google Analytics client ID from _ga cookie
    try {
      const gaCookie = document.cookie.split(';').map(c => c.trim()).find(c => c.startsWith('_ga='))
      if (gaCookie) {
        // _ga=GA1.2.XXXXXXXXXX.XXXXXXXXXX → extract client ID part
        const parts = gaCookie.split('=')[1]?.split('.')
        if (parts && parts.length >= 4) {
          extra.google_analytics_client_id = parts.slice(2).join('.')
        }
      }
    } catch { /* cookie access blocked */ }

    const data = { ...captured, ...extra }
    if (Object.keys(data).length > 0) {
      // First-touch: don't overwrite existing UTM
      if (!sessionStorage.getItem(UTM_KEY)) {
        sessionStorage.setItem(UTM_KEY, JSON.stringify(data))
      }
    }
  } catch { /* SSR / iframe sandbox — ignore */ }
}

/**
 * Get stored UTM data. Returns {} if none captured.
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
  try {
    const raw = sessionStorage.getItem(UTM_KEY)
    if (raw) return JSON.parse(raw)
  } catch { /* storage blocked or bad value: fall through to the page alone */ }
  // Nothing kept (attribution off, or storage blocked): the page the chat is on, without its query.
  try {
    const page_url = window.location.href.split('?')[0]
    return page_url ? { page_url } : {}
  } catch {
    return {}
  }
}
