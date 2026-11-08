import { createFileRoute } from '@tanstack/react-router'
import { Dashboard } from '@/features/dashboard'
import { YourDay } from '@/features/day'
import { isOperatorRole } from '@/components/layout/data/sidebar-data'
import { useAuthStore } from '@/stores/auth-store'

/** Agents' Dashboard is "Your day" (their own chats); everyone else keeps the
 *  team dashboard until it is redesigned. */
function DashboardForRole() {
  const role = useAuthStore((s) => s.auth.user?.role)
  return isOperatorRole(role) ? <YourDay /> : <Dashboard />
}

export const Route = createFileRoute('/_authenticated/')({
  component: DashboardForRole,
})
