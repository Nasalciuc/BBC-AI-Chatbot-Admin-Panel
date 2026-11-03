import { createFileRoute } from '@tanstack/react-router'
import { SecurityBans } from '@/features/security-bans'

export const Route = createFileRoute('/_authenticated/security/bans')({
  component: SecurityBans,
})
