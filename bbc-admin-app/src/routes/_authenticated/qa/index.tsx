import { createFileRoute } from '@tanstack/react-router'
import { QaPage } from '@/features/qa'

export const Route = createFileRoute('/_authenticated/qa/')({
  component: QaPage,
})
