import { z } from 'zod'
import { createFileRoute } from '@tanstack/react-router'
import SetPasswordPage from '@/features/auth/set-password'

const searchSchema = z.object({
  token: z.string().optional(),
})

export const Route = createFileRoute('/(auth)/set-password')({
  component: SetPasswordPage,
  validateSearch: searchSchema,
})
