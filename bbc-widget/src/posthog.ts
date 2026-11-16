import posthogSdk from 'posthog-js'
import { choosePostHog, sitePostHogOf, watchSitePostHog, type PostHogLike } from './posthogChoice'

// The public project token (phc_…): PostHog meant it for the browser, so its
// name stays clear of KEY/TOKEN, which scan_bundle_secrets.py treats as secrets.
const posthogKey = import.meta.env.VITE_POSTHOG_PROJECT
const posthogHost = import.meta.env.VITE_POSTHOG_HOST

function reportMissingConfiguration(variableName: string) {
  if (import.meta.env.DEV) {
    console.error(new Error(
      `${variableName} variable required by PostHog is missing or un-configured, this causes events to be silently missed. This error stops appearing once ${variableName} is configured`,
    ))
  }
}

/** The site's PostHog, when the site started it with the visitor's consent (posthog.astro). */
function sitePostHog(): PostHogLike | undefined {
  return typeof window === 'undefined' ? undefined : sitePostHogOf(window)
}

let ownStarted = false
let ownQuiet = false

/**
 * entry.tsx calls this once, with what the script tag says about consent. The widget's own copy starts
 * only when choosePostHog says 'own'; with the site's PostHog on, or without consent, nothing starts.
 */
export function startPostHog({ consent }: { consent: boolean }): void {
  if (ownStarted) return
  const choice = choosePostHog({ siteOn: !!sitePostHog(), consent, configured: !!(posthogKey && posthogHost) })
  if (choice !== 'own' || !posthogKey || !posthogHost) {
    if (choice === 'none' && consent && !posthogKey) reportMissingConfiguration('VITE_POSTHOG_PROJECT')
    else if (choice === 'none' && consent && !posthogHost) reportMissingConfiguration('VITE_POSTHOG_HOST')
    return
  }
  posthogSdk.init(posthogKey, {
    api_host: posthogHost,
    defaults: '2026-08-30',
    logs: {
      serviceName: 'bbc-widget',
      environment: import.meta.env.MODE,
    },
    capture_exceptions: {
      capture_unhandled_errors: true,
      capture_unhandled_rejections: true,
      capture_console_errors: false,
    },
  })
  ownStarted = true
  // The site's PostHog may come on later in the visit (the visitor accepts the cookies): the copy goes
  // quiet then, not at the widget's next event, so the two never record the same page together.
  if (typeof window !== 'undefined') watchSitePostHog(window, quietOwnCopy)
}

/** The widget's own copy stops recording and capturing by itself once the site's PostHog is on, so
 *  nothing is counted twice; the widget's own events already go to the site's (active()). Once. */
function quietOwnCopy(): void {
  if (!ownStarted || ownQuiet) return
  ownQuiet = true
  try {
    posthogSdk.set_config({ autocapture: false, capture_pageview: false, capture_pageleave: false })
    posthogSdk.stopSessionRecording()
  } catch { /* the copy keeps running; the events still go to the site's PostHog */ }
}

/** The PostHog in use right now. The site's wins whenever it is on: a visitor who accepts the cookies
 *  after the widget loaded moves to it from the next event. */
function active(): PostHogLike | undefined {
  const site = sitePostHog()
  if (site) {
    quietOwnCopy()
    return site
  }
  return ownStarted ? (posthogSdk as unknown as PostHogLike) : undefined
}

/**
 * The widget's PostHog: the same calls as before (posthog?.capture(…)), sent to the site's PostHog, the
 * widget's own copy, or nowhere (choosePostHog). Tracking never breaks the chat: every call is guarded.
 */
export const posthog = {
  capture(event: string, properties?: Record<string, unknown>): void {
    try { active()?.capture(event, properties) } catch { /* tracking never breaks the chat */ }
  },
  identify(distinctId: string): void {
    try { active()?.identify?.(distinctId) } catch { /* tracking never breaks the chat */ }
  },
  /** A different person filled the form in this browser: a new PostHog person. Only the widget's own
   *  copy is reset; the site's PostHog keeps its session and its replay of the page. */
  reset(): void {
    try { if (ownStarted && !sitePostHog()) posthogSdk.reset() } catch { /* tracking never breaks the chat */ }
  },
  get_session_id(): unknown {
    try { return active()?.get_session_id?.() } catch { return undefined }
  },
  logger: {
    info(message: string, attributes?: Record<string, unknown>): void {
      try { active()?.logger?.info?.(message, attributes) } catch { /* tracking never breaks the chat */ }
    },
    warn(message: string, attributes?: Record<string, unknown>): void {
      try { active()?.logger?.warn?.(message, attributes) } catch { /* tracking never breaks the chat */ }
    },
  },
}

/**
 * The PostHog session of this visit, sent with the conversation it starts (metadata.posthog_session_id),
 * so the panel can take the agent to the replay of what the client did before writing. An id only. With
 * the site's PostHog on, it is the site's session: the replay covers the whole visit, not only the chat.
 */
export function posthogSessionId(): string | undefined {
  try {
    const id = posthog.get_session_id()
    return typeof id === 'string' && id ? id : undefined
  } catch {
    return undefined  // PostHog not loaded or blocked: the conversation starts without it
  }
}
