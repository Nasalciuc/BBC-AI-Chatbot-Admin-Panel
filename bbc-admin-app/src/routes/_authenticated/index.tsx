import { createFileRoute, redirect } from '@tanstack/react-router'
import { isEmbedded } from '@/lib/crm-bridge'
import { OwnerDashboard } from '@/features/dashboard/owner'
import { YourDay } from '@/features/day'
import { isOperatorRole } from '@/components/layout/data/sidebar-data'
import { useAuthStore } from '@/stores/auth-store'

/** Agents' Dashboard is "Your day" (their own chats); owner, admin and dev get
 *  the whole business; supervisor and PM their tunnel, about the agents; QA
 *  all chats, quality first. A role the panel doesn't know gets "Your day":
 *  only their own chats, never the business. */
function DashboardForRole() {
  const role = useAuthStore((s) => s.auth.user?.role)
  if (isOperatorRole(role)) return <YourDay />
  if (role === 'owner' || role === 'admin' || role === 'dev') return <OwnerDashboard />
  if (role === 'supervisor' || role === 'project_manager') return <OwnerDashboard variant='team' />
  if (role === 'qa') return <OwnerDashboard variant='qa' />
  return <YourDay />
}

export const Route = createFileRoute('/_authenticated/')({
  // Inside the CRM's chat dock the panel is the chat list and nothing else:
  // no side menu, no tab bar (authenticated-layout.tsx). The dock opens the
  // home (/?sso_token=…), and a Dashboard there had no way to Conversations,
  // so in the dock the home is Conversations. The panel in its own tab keeps
  // the Dashboard.
  beforeLoad: () => {
    if (isEmbedded()) throw redirect({ to: '/chats', replace: true })
  },
  component: DashboardForRole,
})
