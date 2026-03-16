import { createFileRoute } from '@tanstack/react-router'
import WidgetPreview from '@/features/widget-preview'

export const Route = createFileRoute('/widget-preview')({
  component: WidgetPreview,
})
