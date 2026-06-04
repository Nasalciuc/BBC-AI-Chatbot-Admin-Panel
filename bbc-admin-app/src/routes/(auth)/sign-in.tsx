import { z } from 'zod'
import { createFileRoute, redirect } from '@tanstack/react-router'
import { SignIn } from '@/features/auth/sign-in'
import { getCookie } from '@/lib/cookies'

function parseJwtPayload(token: string): Record<string, unknown> | null {
  try {
    const base64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')
    return JSON.parse(atob(base64))
  } catch {
    return null
  }
}

const searchSchema = z.object({
  redirect: z.string().optional(),
})

export const Route = createFileRoute('/(auth)/sign-in')({
  beforeLoad: () => {
    const raw = getCookie('bbc_admin_token')
    if (!raw) return
    let token: string
    try {
      token = JSON.parse(raw) as string
    } catch {
      return
    }
    const claims = parseJwtPayload(token)
    if (claims && typeof claims.exp === 'number' && claims.exp * 1000 > Date.now()) {
      throw redirect({ to: '/' })
    }
  },
  component: SignIn,
  validateSearch: searchSchema,
})
