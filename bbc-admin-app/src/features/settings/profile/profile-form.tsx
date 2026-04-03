import { useState } from 'react'
import { z } from 'zod'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { toast } from 'sonner'
import { useAuthStore } from '@/stores/auth-store'
import { updateUser } from '@/lib/api'
import { BBCAvatar } from '@/components/bbc-avatar'
import { Button } from '@/components/ui/button'
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'

const profileFormSchema = z.object({
  name: z.string().min(2, 'Name must be at least 2 characters.').max(100),
  phone: z
    .string()
    .regex(/^\+?[1-9]\d{6,14}$/, 'Invalid phone (format: +1234567890)')
    .optional()
    .or(z.literal('')),
  avatar_url: z
    .string()
    .url('Must be a valid URL (https://...)')
    .optional()
    .or(z.literal('')),
})

type ProfileFormValues = z.infer<typeof profileFormSchema>

export function ProfileForm() {
  const { auth } = useAuthStore()
  const [showUrlInput, setShowUrlInput] = useState(false)

  const form = useForm<ProfileFormValues>({
    resolver: zodResolver(profileFormSchema),
    defaultValues: {
      name: auth.user?.name || '',
      phone: auth.user?.phone || '',
      avatar_url: auth.user?.avatar_url || '',
    },
    mode: 'onChange',
  })

  // eslint-disable-next-line react-hooks/incompatible-library
  const avatarUrl = form.watch('avatar_url')

  const onSubmit = async (values: ProfileFormValues) => {
    if (!auth.user?.accountNo) {
      toast.error('No user session — please log in again')
      return
    }
    try {
      await updateUser(auth.user.accountNo, {
        name: values.name,
        ...(values.phone ? { phone: values.phone } : {}),
        avatar_url: values.avatar_url || null,
      })
      // Update local auth store so header reflects change immediately
      auth.setUser({
        ...auth.user,
        name: values.name,
        phone: values.phone || auth.user.phone,
        avatar_url: values.avatar_url || null,
      })
      toast.success('Profile saved')
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save profile')
    }
  }

  return (
    <div className="space-y-8">
      {/* Avatar preview + click to edit */}
      <div className="flex flex-col items-start gap-3">
        <p className="text-sm font-medium">Profile Photo</p>
        <BBCAvatar
          name={auth.user?.name || 'User'}
          url={avatarUrl || auth.user?.avatar_url}
          size={80}
          editable
          onClick={() => setShowUrlInput(v => !v)}
        />
        {showUrlInput && (
          <FormField
            control={form.control}
            name="avatar_url"
            render={({ field }) => (
              <FormItem className="w-full max-w-sm">
                <FormControl>
                  <Input
                    placeholder="https://example.com/photo.jpg"
                    autoFocus
                    {...field}
                  />
                </FormControl>
                <FormDescription>
                  Paste a direct link to your profile photo.
                </FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
        )}
      </div>

      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
          {/* Read-only email */}
          <div className="grid grid-cols-6 items-center gap-x-4">
            <p className="col-span-2 text-sm font-medium text-end text-muted-foreground">Email</p>
            <p className="col-span-4 text-sm text-muted-foreground">{auth.user?.email}</p>
          </div>

          {/* Name */}
          <FormField
            control={form.control}
            name="name"
            render={({ field }) => (
              <FormItem className="grid grid-cols-6 items-center space-y-0 gap-x-4 gap-y-1">
                <FormLabel className="col-span-2 text-end">Full Name</FormLabel>
                <FormControl>
                  <Input
                    placeholder="First Last"
                    className="col-span-4"
                    {...field}
                  />
                </FormControl>
                <FormMessage className="col-span-4 col-start-3" />
              </FormItem>
            )}
          />

          {/* Phone */}
          <FormField
            control={form.control}
            name="phone"
            render={({ field }) => (
              <FormItem className="grid grid-cols-6 items-center space-y-0 gap-x-4 gap-y-1">
                <FormLabel className="col-span-2 text-end">Phone</FormLabel>
                <FormControl>
                  <Input
                    placeholder="+1234567890"
                    className="col-span-4"
                    {...field}
                  />
                </FormControl>
                <FormMessage className="col-span-4 col-start-3" />
              </FormItem>
            )}
          />

          <Button type="submit" disabled={form.formState.isSubmitting}>
            {form.formState.isSubmitting ? 'Saving...' : 'Save profile'}
          </Button>
        </form>
      </Form>
    </div>
  )
}
