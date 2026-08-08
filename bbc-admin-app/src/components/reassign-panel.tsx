import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Check, ChevronsUpDown } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
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
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)

  const { data: users = [] } = useQuery({
    queryKey: ['users-for-reassign'],
    queryFn: async () => {
      const res = await getUsers()
      const rows = (res.data ?? []) as UserRow[]
      return rows.filter((u) => ['sales', 'support'].includes(u.role ?? '') && u.is_active)
    },
  })

  const selected = users.find((u) => u.id === selectedAgent)

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
      {/* Combobox: type-to-filter by name/email, full keyboard navigation. */}
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant='outline'
            role='combobox'
            aria-expanded={open}
            className='w-56 justify-between font-normal'
          >
            {selected ? `${selected.name || selected.email} (${selected.role})` : 'Select agent...'}
            <ChevronsUpDown className='ms-2 size-4 shrink-0 opacity-50' />
          </Button>
        </PopoverTrigger>
        <PopoverContent className='w-56 p-0' align='start'>
          <Command>
            <CommandInput placeholder='Search name or email...' />
            <CommandList>
              <CommandEmpty>No operator found.</CommandEmpty>
              <CommandGroup>
                {users.map((u) => (
                  <CommandItem
                    key={u.id}
                    value={`${u.name ?? ''} ${u.email ?? ''}`}
                    onSelect={() => {
                      setSelectedAgent(u.id === selectedAgent ? '' : u.id)
                      setOpen(false)
                    }}
                  >
                    <Check
                      className={cn(
                        'me-2 size-4',
                        selectedAgent === u.id ? 'opacity-100' : 'opacity-0',
                      )}
                    />
                    <div className='grid leading-tight'>
                      <span className='truncate'>{u.name || u.email}</span>
                      <span className='truncate text-xs text-muted-foreground'>
                        {u.email} · {u.role}
                      </span>
                    </div>
                  </CommandItem>
                ))}
              </CommandGroup>
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>
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
