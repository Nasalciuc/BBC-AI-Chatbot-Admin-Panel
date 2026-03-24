import { useState } from 'react'
import { z } from 'zod'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { MailPlus, Send } from 'lucide-react'
import { toast } from 'sonner'
import { inviteUser } from '@/lib/api'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogClose,
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
import { roles } from '../data/data'

const tunnelOptions = [
  { label: 'Sales', value: 'sales' },
  { label: 'Support', value: 'support' },
  { label: 'All', value: 'all' },
]

const phoneRegex = /^\+?[1-9]\d{6,14}$/

const formSchema = z.object({
  name: z.string().min(2, 'Name is required (min 2 chars).'),
  email: z.string().min(1, 'Please enter an email to invite.').email('Invalid email address'),
  phone: z
    .string()
    .regex(phoneRegex, 'Invalid phone number (use E.164: +1234567890)')
    .optional()
    .or(z.literal('')),
  role: z.string().min(1, 'Role is required.'),
  tunnel_scope: z.string().min(1, 'Tunnel is required.'),
  password: z.string().min(8, 'Password must be at least 8 characters.'),
})

type UserInviteForm = z.infer<typeof formSchema>

type UserInviteDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function UsersInviteDialog({
  open,
  onOpenChange,
}: UserInviteDialogProps) {
  const form = useForm<UserInviteForm>({
    resolver: zodResolver(formSchema),
    defaultValues: { name: '', email: '', phone: '', role: '', tunnel_scope: 'sales', password: '' },
  })

  const [isLoading, setIsLoading] = useState(false)

  const onSubmit = async (values: UserInviteForm) => {
    setIsLoading(true)
    try {
      await inviteUser({
        name: values.name,
        email: values.email,
        role: values.role,
        tunnel_scope: values.tunnel_scope,
        password: values.password,
        ...(values.phone ? { phone: values.phone } : {}),
      })
      toast.success(`User ${values.email} invited successfully!`)
      form.reset()
      onOpenChange(false)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to invite user'
      toast.error(message)
    } finally {
      setIsLoading(false)
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
      <DialogContent className='sm:max-w-md'>
        <DialogHeader className='text-start'>
          <DialogTitle className='flex items-center gap-2'>
            <MailPlus /> Invite User
          </DialogTitle>
          <DialogDescription>
            Invite new user to join your team by sending them an email
            invitation. Assign a role to define their access level.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form
            id='user-invite-form'
            onSubmit={form.handleSubmit(onSubmit)}
            className='space-y-4'
          >
            <FormField
              control={form.control}
              name='name'
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Full Name</FormLabel>
                  <FormControl>
                    <Input placeholder='eg: John Doe' {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name='email'
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Email</FormLabel>
                  <FormControl>
                    <Input
                      type='email'
                      placeholder='eg: john.doe@gmail.com'
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name='role'
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Role</FormLabel>
                  <SelectDropdown
                    defaultValue={field.value}
                    onValueChange={field.onChange}
                    placeholder='Select a role'
                    items={roles.map(({ label, value }) => ({
                      label,
                      value,
                    }))}
                  />
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name='tunnel_scope'
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Tunnel</FormLabel>
                  <SelectDropdown
                    defaultValue={field.value}
                    onValueChange={field.onChange}
                    placeholder='Select tunnel'
                    items={tunnelOptions}
                  />
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name='password'
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Temporary Password</FormLabel>
                  <FormControl>
                    <Input type='password' placeholder='Min 8 characters' {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          </form>
        </Form>
        <DialogFooter className='gap-y-2'>
          <DialogClose asChild>
            <Button variant='outline'>Cancel</Button>
          </DialogClose>
          <Button type='submit' form='user-invite-form' disabled={isLoading}>
            Invite <Send />
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
