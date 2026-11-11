import { Link, useLocation } from '@tanstack/react-router'
import { ChevronsUpDown, LogOut, PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import { useAuthStore } from '@/stores/auth-store'
import { useQueueStore } from '@/stores/queue-store'
import { usePermissions } from '@/lib/bbc/hooks'
import type { UserRole } from '@/lib/bbc/types'
import { cn } from '@/lib/utils'
import { menuCount } from '@/features/tasks/view'
import { useTaskAlerts } from '@/features/tasks/use-task-alerts'
import { useReadyToggle } from '@/hooks/use-ready-toggle'
import useDialogState from '@/hooks/use-dialog-state'
import { isEmbedded } from '@/lib/crm-bridge'
import { Sidebar, SidebarContent, SidebarFooter, SidebarHeader, useSidebar } from '@/components/ui/sidebar'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Logo } from '@/assets/logo'
import { SignOutDialog } from '@/components/sign-out-dialog'
import { AvatarPhoto } from '@/components/person-avatar'
import { getNavGroups, isOperatorRole, SETTINGS_ITEM, type PanelNavItem } from './data/sidebar-data'

const ROLE_TEXT: Record<string, string> = {
  owner: 'Owner',
  admin: 'Admin',
  dev: 'Developer',
  qa: 'QA',
  sales: 'Sales agent',
  support: 'Support agent',
  supervisor: 'Supervisor',
  project_manager: 'Project manager',
}

function initialsOf(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (parts.length >= 2) return `${parts[0][0]}${parts[parts.length - 1][0]}`.toUpperCase()
  return name.slice(0, 2).toUpperCase() || 'U'
}

function isActive(pathname: string, url: string): boolean {
  if (url === '/') return pathname === '/'
  return pathname === url || pathname.startsWith(`${url}/`)
}

/**
 * The panel's side menu — the design canvas's menu: brand, the pages for this
 * role, Settings, and the person (with Ready for operators). Dark in both
 * themes. Collapses to icons; on phones it is a sheet.
 */
export function AppSidebar() {
  const { state, toggleSidebar, isMobile } = useSidebar()
  const mini = state === 'collapsed' && !isMobile
  const pathname = useLocation({ select: (l) => l.pathname })
  const user = useAuthStore((s) => s.auth.user)
  const role = (user?.role ?? 'sales') as UserRole
  const permissions = usePermissions(role)
  const groups = getNavGroups(role, permissions)
  const queueCount = useQueueStore((s) => s.queueCount)

  const tasks = useTaskAlerts().data
  const taskCount = menuCount(tasks?.counts, ['owner', 'admin', 'dev', 'supervisor', 'project_manager'].includes(role))

  const badgeFor = (item: PanelNavItem): { text: string; label: string } | null => {
    if (item.key === 'conv' && queueCount > 0 && role !== 'qa') return { text: String(queueCount), label: `${queueCount} waiting for a person` }
    if (item.key === 'tasks' && taskCount > 0) return { text: String(taskCount), label: `${taskCount} late` }
    return null
  }

  return (
    <Sidebar collapsible='icon' variant='sidebar' className='border-none'>
      <SidebarHeader className={cn('px-3.5 pt-5 pb-0', mini && 'px-0 items-center')}>
        <div className={cn('flex items-center gap-2.5', mini ? 'flex-col' : 'ps-1')}>
          {/* The company's logo, as before the redesign (a letter stood in for it). */}
          <Logo alt='' aria-hidden='true' className='size-9 flex-none rounded-[10px]' />
          {!mini && (
            <span className='flex min-w-0 flex-1 flex-col gap-px'>
              <span className='truncate text-[15px] font-bold text-nav-active-fg'>BuyBusinessClass</span>
              <span className='text-xs text-nav-sub'>Chat panel</span>
            </span>
          )}
          {!isMobile && (
            <button
              type='button'
              onClick={toggleSidebar}
              aria-label={mini ? 'Expand the menu' : 'Collapse the menu'}
              title={mini ? 'Expand the menu' : 'Collapse the menu'}
              className='flex size-8 flex-none items-center justify-center rounded-[10px] text-k-9c9c9c hover:bg-nav-hover hover:text-nav-active-fg focus-visible:outline-2 focus-visible:outline-k-6ea8ff'
            >
              {mini ? <PanelLeftOpen className='size-5' /> : <PanelLeftClose className='size-5' />}
            </button>
          )}
        </div>
      </SidebarHeader>

      <SidebarContent className='mt-6 gap-0 px-3'>
        {groups.map((items, gi) => (
          <div key={gi}>
            {gi > 0 && <div aria-hidden='true' className='mx-3 my-3 h-px bg-nav-line' />}
            <nav aria-label={gi === 0 ? 'Panel' : 'More'} className='flex flex-col gap-0.5'>
              {items.map((item) => (
                <NavLink key={item.key} item={item} active={isActive(pathname, item.url)} badge={badgeFor(item)} mini={mini} />
              ))}
            </nav>
          </div>
        ))}
      </SidebarContent>

      <SidebarFooter className='gap-0.5 px-3 pt-3 pb-3.5'>
        <NavLink item={SETTINGS_ITEM} active={isActive(pathname, SETTINGS_ITEM.url)} badge={null} mini={mini} />
        <div aria-hidden='true' className='mx-3 my-2 h-px bg-nav-line' />
        <Me mini={mini} role={role} name={user?.name || 'User'} email={user?.email || ''} photo={user?.avatar_url} />
      </SidebarFooter>
    </Sidebar>
  )
}

function NavLink({
  item,
  active,
  badge,
  mini,
}: {
  item: PanelNavItem
  active: boolean
  badge: { text: string; label: string } | null
  mini: boolean
}) {
  const { setOpenMobile } = useSidebar()
  const Icon = item.icon
  return (
    <Link
      to={item.url}
      onClick={() => setOpenMobile(false)}
      aria-current={active ? 'page' : undefined}
      aria-label={badge ? `${item.title}, ${badge.label}` : item.title}
      title={mini ? item.title : undefined}
      className={cn(
        'relative flex h-10 items-center gap-3 rounded-xl text-sm no-underline transition-colors focus-visible:outline-2 focus-visible:outline-k-6ea8ff',
        mini ? 'mx-auto w-11 justify-center' : 'ps-3 pe-2.5',
        active ? 'bg-nav-active font-bold text-nav-active-fg' : 'text-nav-fg hover:bg-nav-hover'
      )}
    >
      <Icon aria-hidden='true' className='size-5 flex-none' strokeWidth={2} />
      {!mini && <span className='min-w-0 flex-1 truncate'>{item.title}</span>}
      {badge &&
        (mini ? (
          <span aria-hidden='true' className='absolute -top-0.5 -end-0.5 flex h-[18px] min-w-[18px] items-center justify-center rounded-full border-2 border-nav bg-k-d93025 px-1 text-[11px] font-bold text-k-ffffff'>
            {badge.text}
          </span>
        ) : (
          <span aria-hidden='true' className='flex h-5 min-w-[22px] flex-none items-center justify-center rounded-[10px] bg-k-d93025 px-1.5 text-xs font-bold text-k-ffffff'>
            {badge.text}
          </span>
        ))}
    </Link>
  )
}

function Me({
  mini,
  role,
  name,
  email,
  photo,
}: {
  mini: boolean
  role: UserRole
  name: string
  email: string
  photo: string | null | undefined
}) {
  const [open, setOpen] = useDialogState()
  const op = isOperatorRole(role)
  const { ready, toggle } = useReadyToggle()
  const roleText = ROLE_TEXT[role] ?? role
  const sub = op ? (ready ? 'Ready for chats' : 'Not ready') : roleText

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type='button'
            aria-label={`${name}, ${roleText}${op ? `, ${ready ? 'ready for chats' : 'not ready'}` : ''}. Account menu`}
            className={cn(
              'flex w-full items-center gap-2.5 rounded-xl py-2 text-start text-nav-active-fg hover:bg-nav-hover focus-visible:outline-2 focus-visible:outline-k-6ea8ff data-[state=open]:bg-kb-2f2f2f',
              mini ? 'justify-center px-0' : 'px-2.5'
            )}
          >
            <span
              aria-hidden='true'
              className='relative flex size-9 flex-none items-center justify-center rounded-full bg-nav-active text-[13px] font-bold text-nav-active-fg'
            >
              <AvatarPhoto url={photo} fallback={initialsOf(name)} />
              {op && (
                <span
                  className={cn(
                    'absolute -end-px -bottom-px size-[11px] rounded-full border-2 border-nav',
                    ready ? 'bg-k-00c450' : 'bg-k-9c9c9c'
                  )}
                />
              )}
            </span>
            {!mini && (
              <>
                <span className='flex min-w-0 flex-1 flex-col gap-px'>
                  <strong className='truncate text-[13px]'>{name}</strong>
                  <span className={cn('truncate text-xs', op && ready ? 'text-k-5fd394' : 'text-nav-sub')}>{sub}</span>
                </span>
                <ChevronsUpDown aria-hidden='true' className='size-4 flex-none text-k-9c9c9c' />
              </>
            )}
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent side='top' align='start' sideOffset={8} className='w-[var(--radix-dropdown-menu-trigger-width)] min-w-60 rounded-[14px] p-1.5'>
          <div className='flex flex-col gap-0.5 px-3 pt-2.5 pb-2'>
            <strong className='text-sm'>{name}</strong>
            <span className='text-xs break-all text-k-6b6b6b'>{email}</span>
            <span className='text-xs text-k-6b6b6b'>{roleText}</span>
          </div>
          {op && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                onSelect={(e) => {
                  e.preventDefault()
                  toggle()
                }}
                role='menuitemcheckbox'
                aria-checked={ready}
                className='h-10 gap-2.5 rounded-[10px] px-3 text-sm font-bold'
              >
                <span className='flex-1'>Ready for chats</span>
                <span aria-hidden='true' className={cn('relative h-[22px] w-9 flex-none rounded-full', ready ? 'bg-k-00a645' : 'bg-k-c9c9c9')}>
                  <span className={cn('absolute top-[3px] size-4 rounded-full bg-k-ffffff shadow-sm transition-[left]', ready ? 'left-[17px]' : 'left-[3px]')} />
                </span>
              </DropdownMenuItem>
              <p className='px-3 pb-2 text-xs leading-[1.45] text-k-6b6b6b'>
                {ready
                  ? 'New chats from the queue ring for you.'
                  : 'No new chats from the queue. A client who chatted with you before still comes back to you.'}
              </p>
            </>
          )}
          {!isEmbedded() && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={() => setOpen(true)} className='h-10 rounded-[10px] px-3 text-sm'>
                <LogOut aria-hidden='true' />
                Sign out
              </DropdownMenuItem>
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
      <SignOutDialog open={!!open} onOpenChange={setOpen} />
    </>
  )
}
