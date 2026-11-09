import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { type ApiError, createTask } from '@/lib/api'
import type { NewTaskBody } from '@/lib/types'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth-store'
import { useTaskAlerts } from '../use-task-alerts'
import { localZone } from '../view'
import { NewTaskDialog } from './new-task-dialog'

/**
 * "Make a task" on a Dashboard line (Needs attention): a task for yourself,
 * the line kept on it ("From Dashboard · 6 of Rohan Mehta’s 11 chats…").
 * Not drawn before Tasks is set up, or for QA (no Tasks page).
 */
export function MakeTask({ title, line, className }: { title: string; line: string; className?: string }) {
  const qc = useQueryClient()
  const me = useAuthStore((s) => s.auth.user)
  const { data } = useTaskAlerts()
  const [open, setOpen] = useState(false)
  // The due choices ("Today 17:00", "In 1 hour") count from the click, not
  // from the bell's last poll: a dozing tab stops polling, so that could be
  // hours old and "In 1 hour" already past.
  const [openedAt, setOpenedAt] = useState(0)
  const add = useMutation({
    mutationFn: (body: NewTaskBody) => createTask(body),
    onSuccess: () => {
      setOpen(false)
      qc.invalidateQueries({ queryKey: ['tasks'] })
      qc.invalidateQueries({ queryKey: ['notifications'] })
      toast.success('Task added to your Tasks.')
    },
    onError: (e) => toast.error((e as ApiError)?.message ?? 'Couldn’t add the task. Try again.'),
  })
  if (!me || me.role === 'qa' || !data?.set_up || !data.time_zone) return null
  return (
    <>
      <button
        type='button'
        onClick={() => {
          setOpenedAt(Date.now())
          setOpen(true)
        }}
        className={cn('text-sm font-bold text-k-555555 hover:text-k-202020 hover:underline focus-visible:outline-2 focus-visible:outline-k-6ea8ff', className)}
      >
        Make a task
      </button>
      {open && (
        <NewTaskDialog
          open={open}
          onOpenChange={setOpen}
          nowMs={openedAt}
          tz={localZone()}
          meId={me.accountNo}
          people={[]}
          canAssign={false}
          busy={add.isPending}
          onAdd={(body) => add.mutate(body)}
          link={{ label: 'Dashboard', source: 'dashboard', note: line, title }}
        />
      )}
    </>
  )
}
