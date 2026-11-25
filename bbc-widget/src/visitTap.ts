/**
 * The widget's events, as the PostHog wrapper sends them (posthog.ts capture), handed to the visit registry
 * (visits.ts) as well: the registry sees what PostHog sees, for every visitor, without a second call in each place
 * that captures. No imports: posthog.ts and visits.ts both use it, so neither imports the other.
 */

type Listener = (event: string, properties: Record<string, unknown> | undefined) => void

let listener: Listener | null = null

/** visits.ts: the one listener (a second start replaces the first). */
export function onCapture(fn: Listener | null): void {
  listener = fn
}

/** visits.ts's stop: its listener goes, never one a later start put there. */
export function offCapture(fn: Listener): void {
  if (listener === fn) listener = null
}

/** posthog.ts: an event the widget captured. A listener that throws never breaks the event or the chat. */
export function tapCapture(event: string, properties?: Record<string, unknown>): void {
  try { listener?.(event, properties) } catch { /* the registry is never in the chat's way */ }
}
