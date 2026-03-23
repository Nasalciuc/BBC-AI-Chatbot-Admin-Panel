import React from 'react'
import { getRouteApi } from '@tanstack/react-router'
import { type User } from './data/schema'
import { getUsers } from '@/lib/api'
import { ConfigDrawer } from '@/components/config-drawer'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { ProfileDropdown } from '@/components/profile-dropdown'
import { ConnectionBanner } from '@/components/connection-banner'
import { Search } from '@/components/search'
import { ThemeSwitch } from '@/components/theme-switch'
import { UsersDialogs } from './components/users-dialogs'
import { UsersPrimaryButtons } from './components/users-primary-buttons'
import { UsersProvider } from './components/users-provider'
import { UsersTable } from './components/users-table'

const route = getRouteApi('/_authenticated/users/')

export function Users() {
  const search = route.useSearch()
  const navigate = route.useNavigate()
  const [users, setUsers] = React.useState<User[]>([])
  const [loading, setLoading] = React.useState(true)
  const [refreshKey, setRefreshKey] = React.useState(0)

  const refreshUsers = React.useCallback(() => setRefreshKey((k) => k + 1), [])

  React.useEffect(() => {
    setLoading(true)
    getUsers()
      .then((res) => setUsers(res.data as User[]))
      .catch((err) => console.error('[users] API error:', err))
      .finally(() => setLoading(false))
  }, [refreshKey])

  return (
    <UsersProvider onUserChanged={refreshUsers}>
      <Header fixed>
        <Search />
        <div className='ms-auto flex items-center space-x-4'>
          <ConnectionBanner />
          <ThemeSwitch />
          <ConfigDrawer />
          <ProfileDropdown />
        </div>
      </Header>

      <Main className='flex flex-1 flex-col gap-4 sm:gap-6'>
        <div className='flex flex-wrap items-end justify-between gap-2'>
          <div>
            <h2 className='text-2xl font-bold tracking-tight'>User List</h2>
            <p className='text-muted-foreground'>
              Manage your users and their roles here.
            </p>
          </div>
          <UsersPrimaryButtons />
        </div>
        <UsersTable data={loading ? [] : users} search={search} navigate={navigate} />
      </Main>

      <UsersDialogs />
    </UsersProvider>
  )
}
