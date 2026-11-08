import { createFileRoute, redirect } from '@tanstack/react-router'

// The old Notifications page is Alerts now; old links land there.
export const Route = createFileRoute('/_authenticated/settings/notifications')({
  beforeLoad: () => {
    throw redirect({ to: '/settings/alerts', replace: true })
  },
})
