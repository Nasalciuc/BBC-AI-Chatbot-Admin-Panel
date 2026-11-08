import z from 'zod'
import { createFileRoute } from '@tanstack/react-router'
import { Tasks } from '@/features/tasks'

const taskSearchSchema = z.object({
  view: z.enum(['all', 'mine', 'team', 'unowned']).optional().catch(undefined),
  kind: z.enum(['call', 'email', 'todo']).optional().catch(undefined),
  task: z.string().optional().catch(undefined),
  tab: z.enum(['rules']).optional().catch(undefined),
})

export const Route = createFileRoute('/_authenticated/tasks/')({
  validateSearch: taskSearchSchema,
  component: Tasks,
})
