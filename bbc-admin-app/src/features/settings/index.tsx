import { Link, Outlet } from '@tanstack/react-router'
import { canReceiveAssignNotifications, notifyPermission } from '@/lib/notify-assignment'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth-store'
import { HeaderActions } from '@/components/header-actions'
import { PageHeader } from '@/components/layout/page-header'
import { isOperatorRole } from '@/components/layout/data/sidebar-data'

const SECTIONS = [
  { to: '/settings', label: 'Profile' },
  { to: '/settings/alerts', label: 'Alerts' },
  { to: '/settings/security', label: 'Security' },
  { to: '/settings/appearance', label: 'Appearance' },
] as const

/** Settings: your profile, how the panel reaches you, and your sign-in.
 *  Every role sees the same four parts. */
export function Settings() {
  const role = useAuthStore((s) => s.auth.user?.role)
  // A dot on Alerts while this browser can't show a new chat behind other windows.
  const perm = notifyPermission()
  const alertsDot = canReceiveAssignNotifications(role) && (perm === 'default' || perm === 'denied')
  return (
    <main id='content' className='flex flex-col gap-5 px-4 pt-6 pb-9 md:px-7 md:pt-7'>
      <PageHeader
        title='Settings'
        subtitle='Your profile, how the panel reaches you, and your sign-in.'
        actions={<HeaderActions className='ms-0' />}
        // Agents on a phone have the tab bar, not the side menu.
        menuButton={!isOperatorRole(role)}
      />
      <div className='flex flex-col gap-5 lg:flex-row lg:items-start lg:gap-8'>
        <nav aria-label='Settings' className='-mx-1 flex gap-1 overflow-x-auto px-1 lg:mx-0 lg:w-[200px] lg:flex-none lg:flex-col lg:px-0'>
          {SECTIONS.map((s) => {
            const dot = s.label === 'Alerts' && alertsDot
            return (
              <Link
                key={s.to}
                to={s.to}
                activeOptions={{ exact: true }}
                aria-label={dot ? `${s.label}, notifications aren’t on in this browser` : undefined}
                className={cn(
                  'flex h-11 flex-none items-center gap-2 rounded-xl px-3.5 text-[15px] whitespace-nowrap text-k-202020 hover:bg-k-f7f7f7 focus-visible:outline-2 focus-visible:outline-k-6ea8ff',
                  'data-[status=active]:bg-k-f2f2f2 data-[status=active]:font-bold'
                )}
              >
                {s.label}
                {dot && <span aria-hidden='true' className='size-2 rounded-full bg-k-d97a00' />}
              </Link>
            )
          })}
        </nav>
        <div className='flex min-w-0 flex-1 flex-col gap-4 lg:max-w-[760px]'>
          <Outlet />
        </div>
      </div>
    </main>
  )
}
