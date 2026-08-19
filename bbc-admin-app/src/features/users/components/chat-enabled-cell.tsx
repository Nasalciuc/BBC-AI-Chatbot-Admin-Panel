import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { type User } from '../data/schema'
import { updateUser } from '@/lib/api'
import { useAuthStore } from '@/stores/auth-store'
import { Switch } from '@/components/ui/switch'
import { Badge } from '@/components/ui/badge'

/**
 * 033: the management-controlled right to be in the shared chat system.
 *
 * Actionable ONLY for owner/admin — the same server-side guard applies on the
 * PATCH endpoint, so this is presentation, not security. For everyone else the
 * state is shown read-only: a greyed-out switch reads as "broken" and creates
 * support tickets, so the control is simply not offered.
 *
 * The agent's own Ready button is untouched — this is a different axis:
 * whether the account is in the system at all, not whether they are at their
 * desk right now.
 */
export function ChatEnabledCell({ user }: { user: User }) {
  const myRole = useAuthStore((s) => s.auth.user?.role)
  const queryClient = useQueryClient()
  const [busy, setBusy] = useState(false)
  const enabled = user.chat_enabled !== false // absent column = true

  const canToggle = myRole === 'owner' || myRole === 'admin'

  if (!canToggle) {
    return enabled ? null : (
      <Badge variant='outline' className='text-muted-foreground'>
        Fara chat
      </Badge>
    )
  }

  return (
    <Switch
      checked={enabled}
      disabled={busy}
      aria-label='Primeste chaturi'
      onCheckedChange={async (next) => {
        setBusy(true)
        try {
          const res = await updateUser(user.id, { chat_enabled: next })
          if (!res.success) throw new Error('update failed')
          toast.success(
            next
              ? `${user.name} primeste din nou chaturi`
              : `${user.name} nu mai primeste chaturi`
          )
          queryClient.invalidateQueries({ queryKey: ['users'] })
        } catch {
          toast.error('Nu am putut salva — incearca din nou')
        } finally {
          setBusy(false)
        }
      }}
    />
  )
}
