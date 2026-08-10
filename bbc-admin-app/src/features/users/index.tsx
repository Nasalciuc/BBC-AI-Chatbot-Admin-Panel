import React from 'react'
import { getRouteApi } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { type User } from './data/schema'
import { getUsers } from '@/lib/api'
import { Header } from '@/components/layout/header'
import { HeaderActions } from '@/components/header-actions'
import { Main } from '@/components/layout/main'
import { Search } from '@/components/search'
import { UsersDialogs } from './components/users-dialogs'
import { UsersPrimaryButtons } from './components/users-primary-buttons'
import { UsersProvider } from './components/users-provider'
import { UsersTable } from './components/users-table'

const route = getRouteApi('/_authenticated/users/')

export function Users() {
  const search = route.useSearch()
  const navigate = route.useNavigate()
  const queryClient = useQueryClient()

  const { data: users = [], isLoading: loading, isError } = useQuery({
    queryKey: ['users'],
    queryFn: async () => {
      const res = await getUsers()
      if (!res.success) throw new Error('Failed to load users')
      return (res.data ?? []) as User[]
    },
    staleTime: 0,
    refetchOnMount: 'always',
    meta: { errorToast: false },
  })

  React.useEffect(() => {
    if (isError) toast.error('Failed to load users — check your connection')
  }, [isError])

  const refreshUsers = React.useCallback(
    () => queryClient.invalidateQueries({ queryKey: ['users'] }),
    [queryClient],
  )

  return (
    <UsersProvider onUserChanged={refreshUsers}>
      <Header fixed>
        <Search />
        <HeaderActions />
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
