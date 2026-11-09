import { createFileRoute, redirect } from '@tanstack/react-router'
import { AuthenticatedLayout } from '@/components/layout/authenticated-layout'
import { getCookie } from '@/lib/cookies'
import { useAuthStore } from '@/stores/auth-store'
import { withoutSignInTokens } from '@/lib/bands'
import { userFromClaims } from '@/lib/session-swap'

function parseJwtPayload(token: string): Record<string, unknown> | null {
  try {
    const base64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')
    return JSON.parse(atob(base64))
  } catch {
    return null
  }
}

export const Route = createFileRoute('/_authenticated')({
  beforeLoad: ({ location }) => {
    // After signing in you land where you were going (a chat link included).
    const back = withoutSignInTokens(location.href)
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
  },
  component: AuthenticatedLayout,
})
