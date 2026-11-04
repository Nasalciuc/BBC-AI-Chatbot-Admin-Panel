/**
 * Invisible Cloudflare Turnstile (PR-12, owner's decision ⚑1). Only when the
 * embedding <script> carries data-turnstile-sitekey; otherwise no Cloudflare
 * script is loaded at all and the token is null. The server verifies the
 * token (siteverify) — a pass here proves nothing on its own.
 */

let siteKey: string | null = null
let loader: Promise<boolean> | null = null

export function setTurnstileSiteKey(key: string | null | undefined): void {
  siteKey = key && key.trim() ? key.trim() : null
}

type TurnstileApi = {
  render: (el: HTMLElement, opts: Record<string, unknown>) => string
  remove?: (id: string) => void
}

const TURNSTILE_SRC = 'https://challenges.cloudflare.com/turnstile/v0/api.js'
const LOAD_TIMEOUT_MS = 5000

function hasApi(): boolean {
  return !!(window as unknown as { turnstile?: TurnstileApi }).turnstile
}

/** Load api.js once. Races a timeout (a stalled script fires neither load
 *  nor error, and the chat must still start), reuses a tag the host page
 *  already added, checks the global really exists, and forgets a failure so
 *  the next chat can try again. */
function load(): Promise<boolean> {
  if (loader) return loader
  loader = new Promise<boolean>((resolve) => {
    if (hasApi()) return resolve(true)
    let settled = false
    const done = (ok: boolean) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      resolve(ok && hasApi())
    }
    const timer = setTimeout(() => done(false), LOAD_TIMEOUT_MS)
    let s = document.querySelector<HTMLScriptElement>(`script[src^="${TURNSTILE_SRC}"]`)
    if (!s) {
      s = document.createElement('script')
      s.src = `${TURNSTILE_SRC}?render=explicit`
      s.async = true
      document.head.appendChild(s)
    }
    s.addEventListener('load', () => done(true), { once: true })
    s.addEventListener('error', () => done(false), { once: true })
  }).then((ok) => {
    if (!ok) loader = null
    return ok
  })
  return loader
}

/** A fresh Turnstile token, or null (not configured, blocked, or 5 s passed —
 *  the server lets a visitor through when Cloudflare cannot answer). */
export async function getTurnstileToken(): Promise<string | null> {
  if (!siteKey) return null
  const ok = await load()
  const api = (window as unknown as { turnstile?: TurnstileApi }).turnstile
  if (!ok || !api) return null
  return new Promise<string | null>((resolve) => {
    const host = document.createElement('div')
    host.style.position = 'fixed'
    host.style.left = '-10000px'
    host.style.top = '0'
    document.body.appendChild(host)
    let done = false
    let widgetId = ''
    const finish = (token: string | null) => {
      if (done) return
      done = true
      try { if (widgetId) api.remove?.(widgetId) } catch {}
      host.remove()
      resolve(token)
    }
    const timer = setTimeout(() => finish(null), 5000)
    try {
      widgetId = api.render(host, {
        sitekey: siteKey,
        size: 'invisible',
        callback: (t: string) => { clearTimeout(timer); finish(t) },
        'error-callback': () => { clearTimeout(timer); finish(null) },
      })
    } catch {
      clearTimeout(timer)
      finish(null)
    }
  })
}
