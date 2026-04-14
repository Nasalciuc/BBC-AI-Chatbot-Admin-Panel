import { Bell, AlertTriangle } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { getNotifications } from '@/lib/api'
import type { StaleConversation } from '@/lib/types'

export function NotificationBell() {
  const navigate = useNavigate()

  const { data } = useQuery({
    queryKey: ['notifications'],
    queryFn: getNotifications,
    refetchInterval: 30_000,
  })

  const stale: StaleConversation[] = data?.stale_conversations ?? []
  const count = stale.length

  const handleGoToChat = (convId: string) => {
    navigate({ to: '/chats', search: { highlight: convId } })
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant='ghost' size='icon' className='relative'>
          <Bell className='h-5 w-5' />
          {count > 0 && (
            <span className='absolute -top-1 -right-1 h-4 w-4 rounded-full bg-red-500 text-[10px] text-white flex items-center justify-center font-medium'>
              {count > 9 ? '9+' : count}
            </span>
          )}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align='end' className='w-80'>
        <DropdownMenuLabel className='flex items-center gap-2'>
          <Bell className='h-4 w-4' />
          Notifications
          {count > 0 && (
            <span className='ml-auto text-xs text-red-500 font-medium'>
              {count} pending
            </span>
          )}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        {stale.length === 0 ? (
          <div className='p-4 text-sm text-gray-500 text-center'>
            No pending conversations
          </div>
        ) : (
          stale.map((conv) => (
            <DropdownMenuItem
              key={conv.id}
              className='flex items-start gap-3 p-3 cursor-pointer'
              onClick={() => handleGoToChat(conv.id)}
            >
              <AlertTriangle className='h-4 w-4 text-amber-500 mt-0.5 shrink-0' />
              <div className='min-w-0'>
                <p className='text-sm font-medium truncate'>
                  {conv.visitor_name ?? 'Anonymous visitor'}
                </p>
                <p className='text-xs text-gray-500'>
                  Waiting {conv.minutes_waiting} min — {conv.tunnel}
                </p>
              </div>
              <span className='ml-auto text-xs text-red-500 font-medium shrink-0'>
                {conv.minutes_waiting}m
              </span>
            </DropdownMenuItem>
          ))
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
