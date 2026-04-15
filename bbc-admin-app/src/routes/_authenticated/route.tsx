import { createFileRoute, redirect } from '@tanstack/react-router'
import { AuthenticatedLayout } from '@/components/layout/authenticated-layout'
import { getCookie } from '@/lib/cookies'
import { useAuthStore } from '@/stores/auth-store'

function parseJwtPayload(token: string): Record<string, unknown> | null {
  try {
    const base64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')
    return JSON.parse(atob(base64))
  } catch {
    return null
  }
}

export const Route = createFileRoute('/_authenticated')({
  beforeLoad: () => {
    const raw = getCookie('bbc_admin_token')
    if (!raw) {
      throw redirect({ to: '/sign-in' })
    }
    const token = JSON.parse(raw) as string
    const { auth } = useAuthStore.getState()

    // Restore user from JWT if missing (e.g. after page refresh)
    if (!auth.user) {
      const claims = parseJwtPayload(token)
      if (!claims || (typeof claims.exp === 'number' && claims.exp * 1000 < Date.now())) {
        auth.reset()
        throw redirect({ to: '/sign-in' })
      }
      auth.setUser({
        accountNo: (claims.sub as string) || '',
        email: (claims.email as string) || '',
        name: (claims.name as string) || '',
        role: (claims.role as string) || 'sales',
        tunnelScope: (claims.tunnel_scope as string) || '',
        exp: typeof claims.exp === 'number' ? claims.exp * 1000 : Date.now() + 86400000,
        avatar_url: (claims.avatar_url as string) || null,
      })
    }
  },
  component: AuthenticatedLayout,
})
