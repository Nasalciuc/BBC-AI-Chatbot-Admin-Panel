import { z } from 'zod'
import { createFileRoute } from '@tanstack/react-router'
import { GetLink } from '@/features/auth/get-link'

const searchSchema = z.object({
  why: z.enum(['expired', 'lost']).optional().catch(undefined),
  email: z.string().optional(),
})

export const Route = createFileRoute('/(auth)/get-link')({
  component: GetLink,
  validateSearch: searchSchema,
})
