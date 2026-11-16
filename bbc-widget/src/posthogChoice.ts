/**
 * Whose PostHog the widget sends to, and how it recognises the site's. Pure and import-free, so
 * tests/site-signals.test.mjs runs it as it is; posthog.ts does the starting and the sending.
 */

/** What the widget asks of a PostHog: the site's or its own. */
export interface PostHogLike {
  capture: (event: string, properties?: Record<string, unknown>) => unknown
  identify?: (distinctId: string) => unknown
  reset?: () => unknown
  get_session_id?: () => unknown
  logger?: {
    info?: (message: string, attributes?: Record<string, unknown>) => unknown
    warn?: (message: string, attributes?: Record<string, unknown>) => unknown
  }
}

export type PostHogChoice = 'site' | 'own' | 'none'

/**
 * - The site's, once the site started it: posthog.astro sets window.bbcPostHogOn only with the visitor's
 *   consent. One session and one replay for the whole visit, the chat in it, nothing counted twice.
 * - Its own copy only for a visitor with consent on a site that started none (the sites today).
 * - None for a visitor without consent: the site loaded the chat with data-attribution="off" (a tap on
 *   the chat button, no marketing consent), and no PostHog starts behind their back.
 */
export function choosePostHog(o: { siteOn: boolean; consent: boolean; configured: boolean }): PostHogChoice {
  if (o.siteOn) return 'site'
  if (o.consent && o.configured) return 'own'
  return 'none'
}

/**
 * The site's PostHog, only when the site said it started it (window.bbcPostHogOn === true, set by
 * posthog.astro after consent) and it can capture. A window.posthog without that flag (another script's,
 * or the site's before consent) is never used.
 */
export function sitePostHogOf(w: unknown): PostHogLike | undefined {
  try {
    const s = w as { bbcPostHogOn?: unknown; posthog?: PostHogLike } | null | undefined
    const ph = s?.posthog
    return s?.bbcPostHogOn === true && ph && typeof ph.capture === 'function' ? ph : undefined
  } catch {
    return undefined
  }
}
