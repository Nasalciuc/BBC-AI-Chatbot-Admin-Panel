import { useQuery } from '@tanstack/react-query'
import { ConfigDrawer } from '@/components/config-drawer'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { ProfileDropdown } from '@/components/profile-dropdown'
import { ConnectionBanner } from '@/components/connection-banner'
import { NotificationBell } from '@/components/notification-bell'
import { Search } from '@/components/search'
import { ThemeSwitch } from '@/components/theme-switch'
import { TasksDialogs } from './components/tasks-dialogs'
import { TasksPrimaryButtons } from './components/tasks-primary-buttons'
import { TasksProvider } from './components/tasks-provider'
import { TasksTable } from './components/tasks-table'
import { getTasks } from '@/lib/api'

export function Tasks() {
  const { data: tasksData, isLoading } = useQuery({
    queryKey: ['tasks'],
    queryFn: getTasks,
  })
  const tasks = tasksData?.data ?? []

  return (
    <TasksProvider>
      <Header fixed>
        <Search />
        <div className='ms-auto flex items-center space-x-4'>
          <ConnectionBanner />
          <NotificationBell />
          <ThemeSwitch />
          <ConfigDrawer />
          <ProfileDropdown />
        </div>
      </Header>

      <Main className='flex flex-1 flex-col gap-4 sm:gap-6'>
        <div className='flex flex-wrap items-end justify-between gap-2'>
          <div>
            <h2 className='text-2xl font-bold tracking-tight'>Tasks</h2>
            <p className='text-muted-foreground'>
              Here&apos;s a list of your tasks for this month!
            </p>
          </div>
          <TasksPrimaryButtons />
        </div>
        {isLoading ? (
          <div className='flex items-center justify-center h-32 text-muted-foreground text-sm'>Loading tasks...</div>
        ) : (
          <TasksTable data={tasks} />
        )}
      </Main>

      <TasksDialogs />
    </TasksProvider>
  )
}
