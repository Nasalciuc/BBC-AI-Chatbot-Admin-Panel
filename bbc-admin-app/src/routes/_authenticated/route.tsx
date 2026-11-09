import { createFileRoute, redirect } from '@tanstack/react-router'
import { AuthenticatedLayout } from '@/components/layout/authenticated-layout'
import { getCookie } from '@/lib/cookies'
import { useAuthStore } from '@/stores/auth-store'
import { withoutSignInTokens } from '@/lib/bands'
import { userFromClaims } from '@/lib/session-swap'
import { afterCrmSignIn } from '@/lib/sign-in-gate'

function parseJwtPayload(token: string): Record<string, unknown> | null {
  try {
    const base64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')
    return JSON.parse(atob(base64))
  } catch {
    return null
  }
}

function requireSession(href: string): void {
  // After signing in you land where you were going (a chat link included).
  const back = withoutSignInTokens(href)
  const raw = getCookie('bbc_admin_token')
  if (!raw) {
    throw redirect({ to: '/sign-in', search: { redirect: back } })
  }
  const token = JSON.parse(raw) as string
  const { auth } = useAuthStore.getState()

  // Restore user from JWT if missing (e.g. after page refresh)
  if (!auth.user) {
    const claims = parseJwtPayload(token)
    if (!claims || (typeof claims.exp === 'number' && claims.exp * 1000 < Date.now())) {
      auth.reset()
      // An expired session says so on the sign-in page.
      throw redirect({ to: '/sign-in', search: claims ? { redirect: back, session: 'ended' } : { redirect: back } })
    }
    auth.setUser(userFromClaims(claims))
  }
}

export const Route = createFileRoute('/_authenticated')({
  // Opened from the CRM with its sign-in token, the guard first waits for that
  // sign-in (sign-in-gate.ts): on a browser shared between shifts the cookie
  // can still hold the previous agent's session, and nothing below may load
  // from it before the CRM has said who is signing in. Otherwise: at once.
  beforeLoad: ({ location }) => afterCrmSignIn(() => requireSession(location.href)),
  component: AuthenticatedLayout,
})
