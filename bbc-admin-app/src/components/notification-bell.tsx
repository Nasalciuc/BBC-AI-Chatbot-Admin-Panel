import { Bell, AlertTriangle, ClipboardList } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { getNotifications } from '@/lib/api'
import { usePanelModeStore } from '@/stores/panel-mode-store'
import type { StaleConversation, AssignedTask } from '@/lib/types'

export function NotificationBell() {
  const navigate = useNavigate()
  const dormant = usePanelModeStore((s) => s.dormant)

  const { data } = useQuery({
    queryKey: ['notifications'],
    queryFn: getNotifications,
    refetchInterval: dormant ? false : 30_000,
  })

  const stale: StaleConversation[] = data?.stale_conversations ?? []
  const tasks: AssignedTask[] = data?.assigned_tasks ?? []
  const count = stale.length + tasks.length

  const handleGoToChat = (convId: string) => {
    navigate({ to: '/chats', search: { highlight: convId } })
  }

  const handleGoToTasks = () => {
    navigate({ to: '/tasks' })
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type='button'
          aria-label={count > 0 ? `Notifications: ${count} need you` : 'Notifications'}
          className='relative flex size-10 flex-none items-center justify-center rounded-full bg-k-f7f7f7 text-k-202020 hover:bg-k-f2f2f2 focus-visible:outline-2 focus-visible:outline-k-6ea8ff'
        >
          <Bell aria-hidden='true' className='size-[18px]' />
          {count > 0 && (
            <span
              aria-hidden='true'
              className='absolute -top-0.5 -end-0.5 flex h-[18px] min-w-[18px] items-center justify-center rounded-full border-2 border-k-ffffff bg-kb-202020 px-1 text-[11px] font-bold text-on-btn'
            >
              {count > 9 ? '9+' : count}
            </span>
          )}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align='end' className='w-80'>
        <DropdownMenuLabel className='flex items-center gap-2'>
          <Bell className='h-4 w-4' />
          Notifications
          {count > 0 && (
            <span className='ml-auto text-xs text-k-b3261e font-medium'>
              {count} pending
            </span>
          )}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />

        {/* Stale conversations */}
        {stale.map((conv) => (
          <DropdownMenuItem
            key={conv.id}
            className='flex items-start gap-3 p-3 cursor-pointer'
            onClick={() => handleGoToChat(conv.id)}
          >
            <AlertTriangle className='h-4 w-4 text-k-d97a00 mt-0.5 shrink-0' />
            <div className='min-w-0'>
              <p className='text-sm font-medium truncate'>
                {conv.visitor_name ?? 'Anonymous visitor'}
              </p>
              <p className='text-xs text-k-6b6b6b'>
                Waiting {conv.minutes_waiting} min — {conv.tunnel}
              </p>
            </div>
            <span className='ml-auto text-xs text-k-b3261e font-medium shrink-0'>
              {conv.minutes_waiting}m
            </span>
          </DropdownMenuItem>
        ))}

        {/* Separator between sections */}
        {stale.length > 0 && tasks.length > 0 && <DropdownMenuSeparator />}

        {/* Assigned tasks */}
        {tasks.length > 0 && (
          <DropdownMenuLabel className='text-xs text-muted-foreground'>
            Assigned Tasks
          </DropdownMenuLabel>
        )}
        {tasks.map((task) => (
          <DropdownMenuItem
            key={task.id}
            className='flex items-start gap-3 p-3 cursor-pointer'
            onClick={handleGoToTasks}
          >
            <ClipboardList className='h-4 w-4 text-k-1f5fb0 mt-0.5 shrink-0' />
            <div className='min-w-0'>
              <p className='text-sm font-medium truncate'>
                TASK-{String(task.task_number ?? 0).padStart(4, '0')}
              </p>
              <p className='text-xs text-k-6b6b6b truncate'>
                {task.title} — {task.priority}
              </p>
            </div>
          </DropdownMenuItem>
        ))}

        {count === 0 && (
          <div className='p-4 text-sm text-k-6b6b6b text-center'>
            All clear — no notifications
          </div>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
