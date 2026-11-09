import { createFileRoute, redirect } from '@tanstack/react-router'
import { Learning } from '@/features/learning'
import { getPermissions } from '@/lib/bbc/hooks'
import type { UserRole } from '@/lib/bbc/types'
import { useAuthStore } from '@/stores/auth-store'

export const Route = createFileRoute('/_authenticated/learning/')({
  // The menu shows Learning only to who may review it (owner, admin, dev); a
  // typed URL gets the same answer instead of a page of failed loads. The
  // server's role check stays the gate. The user is set by the
  // _authenticated route's beforeLoad, which runs first.
  beforeLoad: () => {
    const role = useAuthStore.getState().auth.user?.role as UserRole | undefined
    if (!role || !getPermissions(role).canReviewLearning) {
      throw redirect({ to: '/403' })
    }
  },
  component: Learning,
})
