import { toast } from 'sonner'
import { apiFetch } from '@/lib/api'
import { useAuthStore } from '@/stores/auth-store'
import { useReadyStore } from '@/stores/ready-store'

/**
 * "Ready for chats" — one toggle for every place that shows it (the page
 * header pill, the account menu, the not-ready banner, the phone's Me tab).
 *
 * Only operators (sales, support) take chats, so only they get a state. Until
 * the first heartbeat answers (null) the pill reads Ready: it corrects itself
 * within 5 s, and a missing button is worse than a briefly optimistic one.
 */
export function useReadyToggle() {
  const role = useAuthStore((s) => s.auth.user?.role)
  const isReady = useReadyStore((s) => s.isReady)
  const setReady = useReadyStore((s) => s.setReady)
  const canBeReady = role === 'sales' || role === 'support'
  const ready = isReady ?? true

  const toggle = async () => {
    const next = !ready
    setReady(next) // optimistic
    try {
      await apiFetch<{ success: boolean; is_ready: boolean }>('/api/agent/ready', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_ready: next }),
      })
      toast.success(next ? 'You’re ready for chats' : 'You’re not ready. New chats won’t come to you.')
    } catch {
      setReady(!next)
      toast.error('Couldn’t change your status. Try again.')
    }
  }

  return { canBeReady, ready, toggle }
}
