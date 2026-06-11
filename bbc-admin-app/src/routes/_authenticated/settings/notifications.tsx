import { createFileRoute, redirect } from '@tanstack/react-router'
import { SettingsNotifications } from '@/features/settings/notifications'
import { getPermissions } from '@/lib/bbc/hooks'
import type { UserRole } from '@/lib/bbc/types'
import { useAuthStore } from '@/stores/auth-store'

export const Route = createFileRoute('/_authenticated/settings/notifications')({
  beforeLoad: () => {
    const user = useAuthStore.getState().auth.user
    const perms = getPermissions((user?.role ?? 'sales') as UserRole)
    if (!perms.canEditSettings) {
      throw redirect({ to: '/settings' })
    }
  },
  component: SettingsNotifications,
})
