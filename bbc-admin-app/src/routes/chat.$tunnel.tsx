import { createFileRoute, redirect } from '@tanstack/react-router'
import ChatStandalone from '@/features/chat-standalone'

export const Route = createFileRoute('/chat/$tunnel')({
  beforeLoad: ({ params }) => {
    if (!['sales', 'support'].includes(params.tunnel)) {
      throw redirect({ to: '/404' })
    }
  },
  component: () => {
    const { tunnel } = Route.useParams()
    return <ChatStandalone tunnel={tunnel as 'sales' | 'support'} />
  },
})
