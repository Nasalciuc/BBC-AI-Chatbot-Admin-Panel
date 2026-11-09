import { Link, useLocation } from '@tanstack/react-router'
import { BookOpen, LayoutGrid, LogOut, MessageCircle, SlidersHorizontal } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth-store'
import { useQueueStore } from '@/stores/queue-store'
import { useReadyToggle } from '@/hooks/use-ready-toggle'
import useDialogState from '@/hooks/use-dialog-state'
import { isEmbedded } from '@/lib/crm-bridge'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { SignOutDialog } from '@/components/sign-out-dialog'

function initialsOf(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (parts.length >= 2) return `${parts[0][0]}${parts[parts.length - 1][0]}`.toUpperCase()
  return name.slice(0, 2).toUpperCase() || 'U'
}

/**
 * An agent on a phone: Chats, Knowledge, Me — the design canvas's phone bar.
 * Phones only (md:hidden), operators only, never inside the CRM dock.
 */
export function MobileTabBar() {
  const pathname = useLocation({ select: (l) => l.pathname })
  const user = useAuthStore((s) => s.auth.user)
  const queueCount = useQueueStore((s) => s.queueCount)
  const { ready, toggle } = useReadyToggle()
  const [signOut, setSignOut] = useDialogState()
  const name = user?.name || 'User'

  const tab = (active: boolean) =>
    cn(
      'flex min-h-14 flex-1 flex-col items-center justify-center gap-1 text-xs focus-visible:outline-2 focus-visible:outline-k-6ea8ff',
      active ? 'font-bold text-k-202020' : 'text-k-6b6b6b'
    )

  return (
    <>
      <nav
        aria-label='Panel'
        className='fixed inset-x-0 bottom-0 z-40 flex border-t border-k-ececec bg-k-ffffff pb-[env(safe-area-inset-bottom)] md:hidden'
      >
        <Link to='/chats' className={tab(pathname.startsWith('/chats'))} aria-label={queueCount ? `Chats, ${queueCount} waiting for a person` : 'Chats'}>
          <span className='relative'>
            <MessageCircle aria-hidden='true' className='size-[22px]' />
            {queueCount > 0 && (
              <span aria-hidden='true' className='absolute -top-1.5 -end-2.5 flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-k-d93025 px-1 text-[11px] font-bold text-k-ffffff'>
                {queueCount}
              </span>
            )}
          </span>
          Chats
        </Link>
        <Link to='/knowledge' className={tab(pathname.startsWith('/knowledge'))}>
          <BookOpen aria-hidden='true' className='size-[22px]' />
          Knowledge
        </Link>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button type='button' className={tab(pathname === '/' || pathname.startsWith('/settings'))} aria-label={`${name}, ${ready ? 'ready for chats' : 'not ready'}. Your menu`}>
              <span aria-hidden='true' className='relative flex size-6 items-center justify-center rounded-full bg-kb-202020 text-[11px] font-bold text-on-btn'>
                {initialsOf(name)}
                <span className={cn('absolute -end-0.5 -bottom-0.5 size-2.5 rounded-full border-2 border-k-ffffff', ready ? 'bg-k-00c450' : 'bg-k-9c9c9c')} />
              </span>
              Me
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent side='top' align='end' sideOffset={8} className='w-64 rounded-[14px] p-1.5'>
            <div className='flex flex-col gap-0.5 px-3 pt-2.5 pb-2'>
              <strong className='text-sm'>{name}</strong>
              <span className='text-xs break-all text-k-6b6b6b'>{user?.email}</span>
            </div>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              role='menuitemcheckbox'
              aria-checked={ready}
              onSelect={(e) => {
                e.preventDefault()
                toggle()
              }}
              className='h-11 gap-2.5 rounded-[10px] px-3 text-sm font-bold'
            >
              <span className='flex-1'>Ready for chats</span>
              <span aria-hidden='true' className={cn('relative h-[22px] w-9 flex-none rounded-full', ready ? 'bg-k-00a645' : 'bg-k-c9c9c9')}>
                <span className={cn('absolute top-[3px] size-4 rounded-full bg-k-ffffff shadow-sm', ready ? 'left-[17px]' : 'left-[3px]')} />
              </span>
            </DropdownMenuItem>
            <p className='px-3 pb-2 text-xs leading-[1.45] text-k-6b6b6b'>On a phone, new chats reach you only while this screen is on.</p>
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild className='h-11 rounded-[10px] px-3 text-sm'>
              <Link to='/'>
                <LayoutGrid aria-hidden='true' />
                Your day
              </Link>
            </DropdownMenuItem>
            <DropdownMenuItem asChild className='h-11 rounded-[10px] px-3 text-sm'>
              <Link to='/settings'>
                <SlidersHorizontal aria-hidden='true' />
                Settings
              </Link>
            </DropdownMenuItem>
            {!isEmbedded() && (
              <DropdownMenuItem onClick={() => setSignOut(true)} className='h-11 rounded-[10px] px-3 text-sm'>
                <LogOut aria-hidden='true' />
                Sign out
              </DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </nav>
      <SignOutDialog open={!!signOut} onOpenChange={setSignOut} />
    </>
  )
}
