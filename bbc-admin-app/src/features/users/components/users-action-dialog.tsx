'use client'

import { z } from 'zod'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import { getUserAccessHistory, inviteUser, updateUser } from '@/lib/api'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { SelectDropdown } from '@/components/select-dropdown'
import { BBCAvatar } from '@/components/bbc-avatar'
import { roles } from '../data/data'
import { type User } from '../data/schema'

const tunnelOptions = [
  { label: 'Sales', value: 'sales' },
  { label: 'Support', value: 'support' },
  { label: 'All', value: 'all' },
]

const formSchema = z
  .object({
    name: z.string().min(2, 'Name is required (min 2 chars).'),
    avatar_url: z
      .string()
      .url('Must be a valid URL')
      .optional()
      .or(z.literal('')),
    email: z.string().min(1, 'Email is required.').email('Invalid email address'),
    phone: z
      .string()
      .regex(/^\+?[1-9]\d{6,14}$/, 'Invalid phone (E.164: +1234567890)')
      .optional()
      .or(z.literal('')),
    role: z.string().min(1, 'Role is required.'),
    tunnel_scope: z.string().min(1, 'Tunnel is required.'),
    isEdit: z.boolean(),
  })
type UserForm = z.infer<typeof formSchema>

type UserActionDialogProps = {
  currentRow?: User
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function UsersActionDialog({
  currentRow,
  open,
  onOpenChange,
}: UserActionDialogProps) {
  const isEdit = !!currentRow
  const { data: accessHistory = [] } = useQuery({
    queryKey: ['user-access-history', currentRow?.id],
    queryFn: async () => {
      if (!currentRow?.id) return []
      const res = await getUserAccessHistory(currentRow.id, 20)
      return res.success ? res.data : []
    },
    enabled: open && isEdit && !!currentRow?.id,
  })
  const form = useForm<UserForm>({
    resolver: zodResolver(formSchema),
    defaultValues: isEdit
      ? {
          name: currentRow?.name ?? '',
          email: currentRow?.email ?? '',
          phone: currentRow?.phone ?? '',
          role: currentRow?.role ?? '',
          tunnel_scope: currentRow?.tunnel_scope ?? 'sales',
          avatar_url: currentRow?.avatar_url ?? '',
          isEdit,
        }
      : {
          name: '',
          email: '',
          phone: '',
          role: '',
          tunnel_scope: 'sales',
          avatar_url: '',
          isEdit,
        },
  })

  const onSubmit = async (values: UserForm) => {
    try {
      if (isEdit && currentRow) {
        await updateUser(currentRow.id, {
          name: values.name,
          role: values.role,
          tunnel_scope: values.tunnel_scope,
          ...(values.phone ? { phone: values.phone } : {}),
          avatar_url: values.avatar_url || null,
        })
        toast.success('User updated')
      } else {
        await inviteUser({
          name: values.name,
          email: values.email,
          role: values.role,
          tunnel_scope: values.tunnel_scope,
        })
        toast.success('User created')
      }
      form.reset()
      onOpenChange(false)
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Operation failed')
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(state) => {
        form.reset()
        onOpenChange(state)
      }}
    >
      <DialogContent className='sm:max-w-lg'>
        <DialogHeader className='text-start'>
          <DialogTitle>{isEdit ? 'Edit User' : 'Add New User'}</DialogTitle>
          <DialogDescription>
            {isEdit ? 'Update the user here. ' : 'Create new user here. '}
            Click save when you&apos;re done.
          </DialogDescription>
        </DialogHeader>
        <div className='h-105 w-[calc(100%+0.75rem)] overflow-y-auto py-1 pe-3'>
          <Form {...form}>
            <form
              id='user-form'
              onSubmit={form.handleSubmit(onSubmit)}
              className='space-y-4 px-0.5'
            >
              {/* Avatar preview */}
              <FormField
                control={form.control}
                name='avatar_url'
                render={({ field }) => (
                  <FormItem className='grid grid-cols-6 items-center space-y-0 gap-x-4 gap-y-1'>
                    <FormLabel className='col-span-2 text-end'>Photo</FormLabel>
                    <div className='col-span-4 flex items-center gap-3'>
                      <BBCAvatar
                        name={form.getValues('name') || 'User'}
                        url={field.value || null}
                        size={48}
                        editable
                      />
                      <FormControl>
                        <Input
                          placeholder='https://example.com/photo.jpg'
                          {...field}
                        />
                      </FormControl>
                    </div>
                    <FormMessage className='col-span-4 col-start-3' />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name='name'
                render={({ field }) => (
                  <FormItem className='grid grid-cols-6 items-center space-y-0 gap-x-4 gap-y-1'>
                    <FormLabel className='col-span-2 text-end'>
                      Name
                    </FormLabel>
                    <FormControl>
                      <Input
                        placeholder='John Doe'
                        className='col-span-4'
                        autoComplete='off'
                        {...field}
                      />
                    </FormControl>
                    <FormMessage className='col-span-4 col-start-3' />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name='email'
                render={({ field }) => (
                  <FormItem className='grid grid-cols-6 items-center space-y-0 gap-x-4 gap-y-1'>
                    <FormLabel className='col-span-2 text-end'>Email</FormLabel>
                    <FormControl>
                      <Input
                        placeholder='john.doe@gmail.com'
                        className='col-span-4'
                        disabled={isEdit}
                        {...field}
                      />
                    </FormControl>
                    <FormMessage className='col-span-4 col-start-3' />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name='phone'
                render={({ field }) => (
                  <FormItem className='grid grid-cols-6 items-center space-y-0 gap-x-4 gap-y-1'>
                    <FormLabel className='col-span-2 text-end'>
                      Phone
                    </FormLabel>
                    <FormControl>
                      <Input
                        placeholder='+1234567890'
                        className='col-span-4'
                        {...field}
                      />
                    </FormControl>
                    <FormMessage className='col-span-4 col-start-3' />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name='role'
                render={({ field }) => (
                  <FormItem className='grid grid-cols-6 items-center space-y-0 gap-x-4 gap-y-1'>
                    <FormLabel className='col-span-2 text-end'>Role</FormLabel>
                    <SelectDropdown
                      defaultValue={field.value}
                      onValueChange={(newRole: string) => {
                        field.onChange(newRole)
                        // Auto-set tunnel based on role
                        const tunnelMap: Record<string, string> = {
                          sales: 'sales',
                          support: 'support',
                          owner: 'all',
                          admin: 'all',
                          qa: 'all',
                        }
                        if (tunnelMap[newRole]) {
                          form.setValue('tunnel_scope', tunnelMap[newRole])
                        }
                      }}
                      placeholder='Select a role'
                      className='col-span-4'
                      items={roles.map(({ label, value }) => ({
                        label,
                        value,
                      }))}
                    />
                    <FormMessage className='col-span-4 col-start-3' />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name='tunnel_scope'
                render={({ field }) => (
                  <FormItem className='grid grid-cols-6 items-center space-y-0 gap-x-4 gap-y-1'>
                    <FormLabel className='col-span-2 text-end'>Tunnel</FormLabel>
                    <SelectDropdown
                      defaultValue={field.value}
                      onValueChange={field.onChange}
                      placeholder='Select tunnel'
                      className='col-span-4'
                      items={tunnelOptions}
                    />
                    <FormMessage className='col-span-4 col-start-3' />
                  </FormItem>
                )}
              />

              {isEdit && (
                <div className='rounded-xl border border-gray-200 bg-gray-50 p-3'>
                  <p className='mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500'>
                    Access Rights History
                  </p>
                  {accessHistory.length === 0 ? (
                    <p className='text-xs text-gray-400 italic'>No access changes recorded yet.</p>
                  ) : (
                    <div className='max-h-40 space-y-2 overflow-y-auto pr-1'>
                      {accessHistory.map((item) => (
                        <div key={item.id} className='rounded-lg border border-gray-200 bg-white px-2.5 py-2'>
                          <p className='text-xs text-gray-700'>
                            {item.changed_by_name || item.changed_by_email || 'Unknown admin'} changed
                            {' '}
                            <span className='font-medium'>{(item.changed_fields || []).join(', ') || 'access rights'}</span>
                          </p>
                          <p className='text-[11px] text-gray-500'>
                            {new Date(item.created_at).toLocaleString()}
                          </p>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

            </form>
          </Form>
        </div>
        <DialogFooter>
          <Button type='submit' form='user-form'>
            Save changes
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
