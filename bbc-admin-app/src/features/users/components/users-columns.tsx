import { type ColumnDef } from '@tanstack/react-table'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Checkbox } from '@/components/ui/checkbox'
import { DataTableColumnHeader } from '@/components/data-table'
import { LongText } from '@/components/long-text'
import { callTypes, roles } from '../data/data'
import { type User } from '../data/schema'
import { DataTableRowActions } from './data-table-row-actions'
import { TeamCell } from './team-cell'
import { ChatEnabledCell } from './chat-enabled-cell'
import { BBCAvatar } from '@/components/bbc-avatar'

export const usersColumns: ColumnDef<User>[] = [
  {
    id: 'select',
    header: ({ table }) => (
      <Checkbox
        checked={
          table.getIsAllPageRowsSelected() ||
          (table.getIsSomePageRowsSelected() && 'indeterminate')
        }
        onCheckedChange={(value) => table.toggleAllPageRowsSelected(!!value)}
        aria-label='Select all'
        className='translate-y-[2px]'
      />
    ),
    meta: {
      className: cn('max-md:sticky start-0 z-10 rounded-tl-[inherit]'),
    },
    cell: ({ row }) => (
      <Checkbox
        checked={row.getIsSelected()}
        onCheckedChange={(value) => row.toggleSelected(!!value)}
        aria-label='Select row'
        className='translate-y-[2px]'
      />
    ),
    enableSorting: false,
    enableHiding: false,
  },
  {
    accessorKey: 'name',
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title='Name' />
    ),
    cell: ({ row }) => {
      const user = row.original
      return (
        <div className="flex items-center gap-2">
          <BBCAvatar
            name={user.name || 'User'}
            url={(user as Record<string, unknown>).avatar_url as string | undefined}
            size={32}
          />
          <LongText>{row.getValue('name')}</LongText>
        </div>
      )
    },
    meta: {
      className: cn(
        'drop-shadow-[0_1px_2px_rgb(0_0_0_/_0.1)] dark:drop-shadow-[0_1px_2px_rgb(255_255_255_/_0.1)]',
        'ps-0.5 max-md:sticky start-6 @4xl/content:table-cell @4xl/content:drop-shadow-none'
      ),
    },
    enableHiding: false,
  },
  {
    accessorKey: 'email',
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title='Email' />
    ),
    cell: ({ row }) => (
      <div className='w-fit ps-2 text-nowrap'>{row.getValue('email')}</div>
    ),
  },
  {
    accessorKey: 'phone',
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title='Phone' />
    ),
    cell: ({ row }) => (
      <div className='text-nowrap'>{row.getValue('phone') || '\u2014'}</div>
    ),
    enableSorting: false,
  },
  {
    accessorKey: 'tunnel_scope',
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title='Tunnel' />
    ),
    cell: ({ row }) => (
      <Badge variant='outline' className='capitalize'>
        {row.getValue('tunnel_scope')}
      </Badge>
    ),
    enableSorting: false,
  },
  {
    accessorKey: 'team_id',
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title='Team' />
    ),
    cell: ({ row }) => <TeamCell teamId={row.original.team_id} />,
    enableSorting: false,
  },
  {
    accessorKey: 'is_active',
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title='Status' />
    ),
    cell: ({ row }) => {
      const active = row.getValue('is_active') as boolean
      const lastSeen = row.original.last_seen_at
      const status = !active ? 'inactive' : lastSeen ? 'active' : 'invited'
      const badgeColor = callTypes.get(status)
      return (
        <Badge variant='outline' className={cn('capitalize', badgeColor)}>
          {status === 'invited' ? 'Invited' : status === 'active' ? 'Active' : 'Inactive'}
        </Badge>
      )
    },
    filterFn: (row, id, value) => {
      const active = row.getValue(id) as boolean
      const lastSeen = row.original.last_seen_at
      const status = !active ? 'inactive' : lastSeen ? 'active' : 'invited'
      return value.includes(status)
    },
    enableHiding: false,
    enableSorting: false,
  },
  {
    accessorKey: 'chat_enabled',
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title='Primeste chaturi' />
    ),
    cell: ({ row }) => <ChatEnabledCell user={row.original} />,
    enableSorting: false,
  },
  {
    accessorKey: 'role',
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title='Role' />
    ),
    cell: ({ row }) => {
      const { role } = row.original
      const userType = roles.find(({ value }) => value === role)

      if (!userType) {
        return null
      }

      return (
        <div className='flex items-center gap-x-2'>
          {userType.icon && (
            <userType.icon size={16} className='text-muted-foreground' />
          )}
          <span className='text-sm'>{userType.label}</span>
        </div>
      )
    },
    filterFn: (row, id, value) => {
      return value.includes(row.getValue(id))
    },
    enableSorting: false,
    enableHiding: false,
  },
  {
    id: 'actions',
    cell: DataTableRowActions,
  },
]
