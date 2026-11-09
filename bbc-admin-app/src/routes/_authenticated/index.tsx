import { createFileRoute } from '@tanstack/react-router'
import { Dashboard } from '@/features/dashboard'
import { OwnerDashboard } from '@/features/dashboard/owner'
import { YourDay } from '@/features/day'
import { isOperatorRole } from '@/components/layout/data/sidebar-data'
import { useAuthStore } from '@/stores/auth-store'

/** Agents' Dashboard is "Your day" (their own chats); owner, admin and dev get
 *  the whole business; supervisor, PM and QA keep the team dashboard until
 *  theirs is redesigned. */
function DashboardForRole() {
  const role = useAuthStore((s) => s.auth.user?.role)
  if (isOperatorRole(role)) return <YourDay />
  if (role === 'owner' || role === 'admin' || role === 'dev') return <OwnerDashboard />
  return <Dashboard />
}

export const Route = createFileRoute('/_authenticated/')({
  component: DashboardForRole,
})
