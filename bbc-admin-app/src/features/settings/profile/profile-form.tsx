import { useRef, useState } from 'react'
import { z } from 'zod'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { toast } from 'sonner'
import { useAuthStore } from '@/stores/auth-store'
import { updateSelf, uploadAvatar } from '@/lib/api'
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

const MAX_AVATAR_BYTES = 2_000_000
const ALLOWED_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp'])

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
  const [uploading, setUploading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

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
    if (!auth.user) {
      toast.error('No user session — please log in again')
      return
    }
    try {
      const response = await updateSelf({
        name: values.name,
        phone: values.phone ?? '',
        avatar_url: values.avatar_url ?? '',
      })
      auth.setAccessToken(response.token)
      auth.setUser({
        accountNo: response.user.id,
        email: response.user.email,
        name: response.user.name || '',
        role: response.user.role || auth.user.role,
        tunnelScope: response.user.tunnel_scope || auth.user.tunnelScope,
        exp: Date.now() + 24 * 60 * 60 * 1000,
        avatar_url: response.user.avatar_url ?? null,
        phone: response.user.phone ?? '',
      })
      toast.success('Profile saved')
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save profile')
    }
  }

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    // Allow re-selecting the same file later
    e.target.value = ''
    if (!f || !auth.user) return

    if (!ALLOWED_TYPES.has(f.type)) {
      toast.error('Unsupported image type — use JPEG, PNG or WEBP (or paste a URL)')
      return
    }
    if (f.size > MAX_AVATAR_BYTES) {
      toast.error('Image too large (max 2MB) — or paste a URL instead')
      return
    }

    setUploading(true)
    try {
      const res = await uploadAvatar(f)
      auth.setAccessToken(res.token)
      auth.setUser({
        ...auth.user,
        avatar_url: res.avatar_url,
      })
      form.setValue('avatar_url', res.avatar_url, { shouldDirty: false })
      toast.success('Photo updated')
    } catch (err) {
      toast.error(
        err instanceof Error
          ? err.message
          : 'Upload failed — you can paste a URL instead',
      )
    } finally {
      setUploading(false)
    }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-8">

        {/* Avatar — INSIDE Form so FormField has context */}
        <div className="flex flex-col items-start gap-3">
          <p className="text-sm font-medium">Profile Photo</p>
          <BBCAvatar
            name={auth.user?.name || 'User'}
            url={avatarUrl || auth.user?.avatar_url}
            size={80}
            editable
            onClick={() => !uploading && fileInputRef.current?.click()}
          />
          <input
            ref={fileInputRef}
            type="file"
            accept="image/jpeg,image/png,image/webp"
            className="hidden"
            onChange={onFile}
            disabled={uploading}
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={uploading}
              onClick={() => fileInputRef.current?.click()}
            >
              {uploading ? 'Uploading…' : 'Upload photo'}
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={uploading}
              onClick={() => setShowUrlInput(v => !v)}
            >
              {showUrlInput ? 'Hide URL' : 'Paste URL instead'}
            </Button>
          </div>
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
                      className="bg-background text-foreground border-input"
                      {...field}
                    />
                  </FormControl>
                  <FormDescription>
                    Fallback: paste a direct link if upload is unavailable.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
          )}
        </div>

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
                  className="col-span-4 bg-background text-foreground border-input"
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
                  className="col-span-4 bg-background text-foreground border-input"
                  {...field}
                />
              </FormControl>
              <FormMessage className="col-span-4 col-start-3" />
            </FormItem>
          )}
        />

        <Button type="submit" disabled={form.formState.isSubmitting || uploading}>
          {form.formState.isSubmitting ? 'Saving...' : 'Save profile'}
        </Button>

      </form>
    </Form>
  )
}
