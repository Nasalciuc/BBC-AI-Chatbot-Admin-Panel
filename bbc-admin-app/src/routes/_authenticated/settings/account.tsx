import { createFileRoute, redirect } from '@tanstack/react-router'

// The template's Account page is gone; old links land on Profile.
export const Route = createFileRoute('/_authenticated/settings/account')({
  beforeLoad: () => {
    throw redirect({ to: '/settings', replace: true })
  },
})
