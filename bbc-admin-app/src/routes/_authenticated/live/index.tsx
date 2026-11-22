import { createFileRoute } from '@tanstack/react-router'
import { LiveVisitors } from '@/features/live'

export const Route = createFileRoute('/_authenticated/live/')({
  component: LiveVisitors,
})
