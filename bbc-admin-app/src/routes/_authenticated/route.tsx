import { createFileRoute, redirect } from '@tanstack/react-router'
import { AuthenticatedLayout } from '@/components/layout/authenticated-layout'
import { getCookie } from '@/lib/cookies'

export const Route = createFileRoute('/_authenticated')({
  beforeLoad: () => {
    const token = getCookie('bbc_admin_token')
    if (!token) {
      throw redirect({ to: '/sign-in' })
    }
  },
  component: AuthenticatedLayout,
})
