import posthogSdk from 'posthog-js'

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

export const posthog = (() => {
  if (!posthogKey) {
    reportMissingConfiguration('VITE_POSTHOG_PROJECT')
    return undefined
  }

  if (!posthogHost) {
    reportMissingConfiguration('VITE_POSTHOG_HOST')
    return undefined
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

  return posthogSdk
})()
