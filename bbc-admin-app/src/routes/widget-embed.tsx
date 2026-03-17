import { createFileRoute } from '@tanstack/react-router'
import WidgetEmbed from '@/features/widget-embed'

export const Route = createFileRoute('/widget-embed')({
  component: WidgetEmbed,
})
