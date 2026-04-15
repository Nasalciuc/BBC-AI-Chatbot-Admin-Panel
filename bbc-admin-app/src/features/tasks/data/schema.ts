import { z } from 'zod'

export const taskSchema = z.object({
  id: z.string(),
  task_number: z.number().optional().nullable(),
  title: z.string(),
  description: z.string().nullable(),
  status: z.enum(['todo', 'in progress', 'done', 'canceled', 'backlog']),
  label: z.enum(['bug', 'feature', 'documentation']),
  priority: z.enum(['low', 'medium', 'high', 'critical']),
  assignee_id: z.string().nullable(),
  assignee_name: z.string().nullable(),
  created_by: z.string().nullable(),
  due_date: z.string().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
})

export type Task = z.infer<typeof taskSchema>
