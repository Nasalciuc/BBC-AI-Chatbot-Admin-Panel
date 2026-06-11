import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { apiFetch } from '@/lib/api'
import { useAuthStore } from '@/stores/auth-store'
import { useReadyStore } from '@/stores/ready-store'

export function ReadyToggle() {
  const { auth } = useAuthStore()
  const user = auth.user
  const { isReady, setReady } = useReadyStore()

  // Hide for management/read-only roles
  if (!user || ['owner', 'admin', 'dev', 'supervisor', 'qa'].includes(user.role ?? '')) {
    return null
  }

  // While the first heartbeat hasn't returned yet (null), optimistically show Ready
  // so the button isn't missing on mount. It corrects itself within 5s.
  const ready = isReady ?? true

  const toggle = async () => {
    const newStatus = !ready
    // Optimistic update
    setReady(newStatus)
    try {
      await apiFetch<{ success: boolean; is_ready: boolean }>('/api/agent/ready', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_ready: newStatus }),
      })
      toast.success(newStatus ? 'You are now Ready' : 'You are now Not Ready')
    } catch {
      // Roll back optimistic update on failure
      setReady(!newStatus)
      toast.error('Failed to update status')
    }
  }

  return (
    <Button
      variant='outline'
      size='sm'
      onClick={toggle}
      className={`text-xs font-semibold border-2 transition-colors ${
        ready
          ? 'border-green-500 text-green-600 hover:bg-green-50'
          : 'border-red-500 text-red-600 hover:bg-red-50'
      }`}
    >
      <span className={`w-2 h-2 rounded-full mr-2 ${ready ? 'bg-green-500' : 'bg-red-500'}`} />
      {ready ? 'Ready' : 'Not Ready'}
    </Button>
  )
}
