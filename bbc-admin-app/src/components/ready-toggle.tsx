import { useState } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { apiFetch } from '@/lib/api'
import { useAuthStore } from '@/stores/auth-store'

export function ReadyToggle() {
  const { auth } = useAuthStore()
  const user = auth.user
  const [isReady, setIsReady] = useState(true)
  const [loading, setLoading] = useState(false)

  if (!user || ['owner', 'admin', 'dev', 'supervisor'].includes(user.role ?? '')) {
    return null
  }

  const toggle = async () => {
    setLoading(true)
    try {
      const newStatus = !isReady
      await apiFetch<{ success: boolean; is_ready: boolean }>('/api/agent/ready', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_ready: newStatus }),
      })
      setIsReady(newStatus)
      toast.success(newStatus ? 'You are now Ready' : 'You are now Not Ready')
    } catch {
      toast.error('Failed to update status')
    } finally {
      setLoading(false)
    }
  }

  return (
    <Button
      variant='outline'
      size='sm'
      onClick={toggle}
      disabled={loading}
      className={`text-xs font-semibold border-2 transition-colors ${
        isReady
          ? 'border-green-500 text-green-600 hover:bg-green-50'
          : 'border-red-500 text-red-600 hover:bg-red-50'
      }`}
    >
      <span className={`w-2 h-2 rounded-full mr-2 ${isReady ? 'bg-green-500' : 'bg-red-500'}`} />
      {isReady ? 'Ready' : 'Not Ready'}
    </Button>
  )
}
