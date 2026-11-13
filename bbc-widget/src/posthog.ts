import posthogSdk from 'posthog-js'

const posthogKey = import.meta.env.VITE_POSTHOG_KEY
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
    reportMissingConfiguration('VITE_POSTHOG_KEY')
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
