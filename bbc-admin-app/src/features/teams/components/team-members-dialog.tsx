import { useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Check, ChevronsUpDown, X } from 'lucide-react'
import { toast } from 'sonner'
import { cn } from '@/lib/utils'
import type { Team } from '@/lib/bbc/types'
import { assignUserToTeam } from '@/lib/api'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
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
import { type TeamUser, apiErrorMessage } from '../data/types'

const OPERATOR_ROLES = ['sales', 'support']

type Props = {
  open: boolean
  onOpenChange: (open: boolean) => void
  team: Team
  users: TeamUser[]
}

export function TeamMembersDialog({ open, onOpenChange, team, users }: Props) {
  const queryClient = useQueryClient()
  const [pendingId, setPendingId] = useState<string | null>(null)
  const [addValue, setAddValue] = useState<string>('')
  const [pickerOpen, setPickerOpen] = useState(false)

  const members = useMemo(
    () => users.filter((u) => u.team_id === team.id),
    [users, team.id],
  )

  // Eligible operators not already in this team.
  const eligible = useMemo(
    () =>
      users.filter(
        (u) => OPERATOR_ROLES.includes(u.role ?? '') && u.team_id !== team.id,
      ),
    [users, team.id],
  )

  const selectedLabel = useMemo(() => {
    const u = eligible.find((o) => o.id === addValue)
    return u ? `${u.name || u.email || u.id} (${u.role})` : null
  }, [eligible, addValue])

  const refresh = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ['teams'] }),
      queryClient.invalidateQueries({ queryKey: ['teams-users'] }),
      queryClient.invalidateQueries({ queryKey: ['users'] }),
    ])

  const add = async (userId: string) => {
    if (!userId) return
    setPendingId(userId)
    try {
      await assignUserToTeam(userId, team.id)
      await refresh()
      setAddValue('')
      toast.success('Operator added to team')
    } catch (err) {
      toast.error(apiErrorMessage(err, 'Failed to add operator'))
    } finally {
      setPendingId(null)
    }
  }

  const remove = async (userId: string) => {
    setPendingId(userId)
    try {
      await assignUserToTeam(userId, null)
      await refresh()
      toast.success('Operator removed from team')
    } catch (err) {
      toast.error(apiErrorMessage(err, 'Failed to remove operator'))
    } finally {
      setPendingId(null)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className='sm:max-w-lg'>
        <DialogHeader className='text-start'>
          <DialogTitle>Manage members — {team.name}</DialogTitle>
          <DialogDescription>
            An operator belongs to one team. Adding one here moves them from any previous team.
          </DialogDescription>
        </DialogHeader>

        <div className='space-y-4'>
          <div className='flex items-end gap-2'>
            <div className='min-w-0 flex-1'>
              <label className='mb-1 block text-sm font-medium'>Add operator</label>
              <Popover open={pickerOpen} onOpenChange={setPickerOpen}>
                <PopoverTrigger asChild>
                  <Button
                    variant='outline'
                    role='combobox'
                    aria-expanded={pickerOpen}
                    disabled={eligible.length === 0}
                    className='w-full justify-between font-normal'
                  >
                    <span className='truncate'>
                      {selectedLabel ??
                        (eligible.length === 0
                          ? 'No eligible operators'
                          : 'Select an operator')}
                    </span>
                    <ChevronsUpDown className='ms-2 h-4 w-4 shrink-0 opacity-50' />
                  </Button>
                </PopoverTrigger>
                <PopoverContent className='w-(--radix-popover-trigger-width) p-0' align='start'>
                  <Command>
                    <CommandInput placeholder='Search operators…' />
                    <CommandList>
                      <CommandEmpty>No operator found.</CommandEmpty>
                      <CommandGroup>
                        {eligible.map((u) => {
                          const label = `${u.name || u.email || u.id} (${u.role})`
                          return (
                            <CommandItem
                              key={u.id}
                              value={label}
                              onSelect={() => {
                                setAddValue(u.id)
                                setPickerOpen(false)
                              }}
                            >
                              <Check
                                className={cn(
                                  'me-2 h-4 w-4',
                                  addValue === u.id ? 'opacity-100' : 'opacity-0',
                                )}
                              />
                              {label}
                            </CommandItem>
                          )
                        })}
                      </CommandGroup>
                    </CommandList>
                  </Command>
                </PopoverContent>
              </Popover>
            </div>
            <Button
              onClick={() => add(addValue)}
              disabled={!addValue || pendingId === addValue}
            >
              Add
            </Button>
          </div>

          <div>
            <p className='mb-2 text-sm font-medium'>
              Current members ({members.length})
            </p>
            {members.length === 0 ? (
              <p className='text-sm text-muted-foreground italic'>No members yet.</p>
            ) : (
              <ul className='max-h-64 space-y-1 overflow-y-auto'>
                {members.map((u) => (
                  <li
                    key={u.id}
                    className='flex items-center justify-between rounded-md border px-3 py-2 text-sm'
                  >
                    <span>
                      {u.name || u.email || u.id}
                      <span className='ms-2 text-xs text-muted-foreground'>{u.role}</span>
                    </span>
                    <Button
                      variant='ghost'
                      size='icon'
                      className='h-7 w-7'
                      disabled={pendingId === u.id}
                      onClick={() => remove(u.id)}
                      aria-label={`Remove ${u.name || u.email || u.id}`}
                    >
                      <X className='h-4 w-4' />
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button variant='outline' onClick={() => onOpenChange(false)}>
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
