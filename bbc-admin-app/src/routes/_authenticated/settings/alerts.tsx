import { createFileRoute } from '@tanstack/react-router'
import { SettingsAlerts } from '@/features/settings/alerts'

export const Route = createFileRoute('/_authenticated/settings/alerts')({
  component: SettingsAlerts,
})
