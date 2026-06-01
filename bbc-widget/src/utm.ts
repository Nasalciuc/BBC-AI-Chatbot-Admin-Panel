// bbc-widget/src/utm.ts — UTM capture from URL params (first-touch)

const UTM_KEY = 'bbc_utm'
const UTM_PARAMS = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content'] as const

export type UtmFields = Partial<Record<(typeof UTM_PARAMS)[number], string>>

/**
 * Capture UTM params from current URL into sessionStorage (first-touch).
 * Call once at widget init — before render.
 */
export function captureUtm(): void {
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
export function getUtm(): UtmFields & { page_url?: string; referrer?: string } {
  try {
    const raw = sessionStorage.getItem(UTM_KEY)
    return raw ? JSON.parse(raw) : {}
  } catch {
    return {}
  }
}
