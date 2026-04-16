import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { getUsers, reassignConversation } from '@/lib/api'

type UserRow = {
  id: string
  name?: string
  email?: string
  role?: string
  is_active?: boolean
}

interface Props {
  conversationId?: string
  onReassigned?: () => void
}

export function ReassignPanel({ conversationId, onReassigned }: Props) {
  const [selectedAgent, setSelectedAgent] = useState('')
  const [loading, setLoading] = useState(false)

  const { data: users = [] } = useQuery({
    queryKey: ['users-for-reassign'],
    queryFn: async () => {
      const res = await getUsers()
      const rows = (res.data ?? []) as UserRow[]
      return rows.filter((u) => ['sales', 'support'].includes(u.role ?? '') && u.is_active)
    },
  })

  const handleReassign = async () => {
    if (!conversationId || !selectedAgent) return
    setLoading(true)
    try {
      await reassignConversation(conversationId, selectedAgent)
      toast.success('Conversation reassigned successfully')
      onReassigned?.()
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to reassign'
      toast.error(message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className='flex gap-2 items-center'>
      <Select value={selectedAgent} onValueChange={setSelectedAgent}>
        <SelectTrigger className='w-56'>
          <SelectValue placeholder='Select agent...' />
        </SelectTrigger>
        <SelectContent>
          {users.map((u) => (
            <SelectItem key={u.id} value={u.id}>
              {u.name || u.email} ({u.role})
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Button
        onClick={handleReassign}
        disabled={!selectedAgent || loading}
        size='sm'
      >
        {loading ? 'Reassigning...' : 'Reassign'}
      </Button>
    </div>
  )
}
