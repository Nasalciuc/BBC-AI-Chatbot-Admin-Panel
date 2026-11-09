import z from 'zod'
import { createFileRoute, redirect } from '@tanstack/react-router'
import { Leads } from '@/features/leads'
import { getCookie } from '@/lib/cookies'
import { useAuthStore } from '@/stores/auth-store'

// Links from the Dashboard open a tab and a level: /leads?view=all&level=ready
const leadsSearchSchema = z.object({
  view: z.enum(['not_in_crm', 'to_review', 'reviewed', 'all']).optional().catch(undefined),
  level: z.enum(['ready', 'almost', 'early', 'contact']).optional().catch(undefined),
})

export const Route = createFileRoute('/_authenticated/leads/')({
  validateSearch: leadsSearchSchema,
  beforeLoad: () => {
    const { auth } = useAuthStore.getState()
    let role = auth.user?.role

    if (!role) {
      const raw = getCookie('bbc_admin_token')
      if (raw) {
        try {
          const token = JSON.parse(raw) as string
          const payload = JSON.parse(
            atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')),
          ) as { role?: string }
          role = payload.role
        } catch {
          // fall through to redirect
        }
      }
    }

    if (!role || !['owner', 'admin', 'qa'].includes(role)) {
      throw redirect({ to: '/chats' })
    }
  },
  component: Leads,
})
