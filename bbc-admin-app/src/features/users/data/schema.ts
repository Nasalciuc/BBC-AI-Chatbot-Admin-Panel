import { z } from 'zod'

const userStatusSchema = z.union([
  z.literal('active'),
  z.literal('inactive'),
  z.literal('invited'),
])
export type UserStatus = z.infer<typeof userStatusSchema>

const userRoleSchema = z.union([
  z.literal('owner'),
  z.literal('admin'),
  z.literal('sales'),
  z.literal('support'),
  z.literal('supervisor'),
  z.literal('qa'),
  z.literal('project_manager'),
])

const userSchema = z.object({
  id: z.string(),
  name: z.string(),
  email: z.string(),
  phone: z.string().nullable().optional(),
  role: userRoleSchema,
  tunnel_scope: z.string(),
  is_active: z.boolean(),
  last_seen_at: z.string().nullable().optional(),
  avatar_url: z.string().nullable().optional(),
  created_at: z.coerce.date(),
  updated_at: z.coerce.date(),
})
export type User = z.infer<typeof userSchema>

export const userListSchema = z.array(userSchema)
