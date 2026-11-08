import { createFileRoute } from '@tanstack/react-router'
import { Dashboard } from '@/features/dashboard'
import { OwnerDashboard } from '@/features/dashboard/owner'
import { YourDay } from '@/features/day'
import { isOperatorRole } from '@/components/layout/data/sidebar-data'
import { useAuthStore } from '@/stores/auth-store'

/** Agents' Dashboard is "Your day" (their own chats); owner, admin and dev get
 *  the whole business; supervisor and PM their tunnel, about the agents; QA
 *  all chats, quality first. Any other role keeps the old team dashboard. */
function DashboardForRole() {
  const role = useAuthStore((s) => s.auth.user?.role)
  if (isOperatorRole(role)) return <YourDay />
  if (role === 'owner' || role === 'admin' || role === 'dev') return <OwnerDashboard />
  if (role === 'supervisor' || role === 'project_manager') return <OwnerDashboard variant='team' />
  if (role === 'qa') return <OwnerDashboard variant='qa' />
  return <Dashboard />
}

export const Route = createFileRoute('/_authenticated/')({
  component: DashboardForRole,
})
