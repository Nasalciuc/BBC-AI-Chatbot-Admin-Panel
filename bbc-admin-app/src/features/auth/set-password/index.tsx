import { useState } from 'react'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { z } from 'zod'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { PasswordInput } from '@/components/password-input'
import {
  Form, FormControl, FormField, FormItem, FormLabel, FormMessage
} from '@/components/ui/form'
import { apiFetch } from '@/lib/api'

const formSchema = z.object({
  password: z.string().min(8, 'Password must be at least 8 characters'),
  confirm: z.string().min(8, 'Please confirm your password'),
}).refine(data => data.password === data.confirm, {
  message: "Passwords don't match",
  path: ['confirm'],
})

type FormValues = z.infer<typeof formSchema>

export default function SetPasswordPage() {
  const navigate = useNavigate()
  const search = useSearch({ strict: false }) as { token?: string }
  const token = search?.token ?? ''
  const [isLoading, setIsLoading] = useState(false)

  const form = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: { password: '', confirm: '' },
  })

  const onSubmit = async (values: FormValues) => {
    if (!token) {
      toast.error('Invalid or missing token')
      return
    }
    setIsLoading(true)
    try {
      await apiFetch('/api/auth/set-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, password: values.password }),
      })
      toast.success('Password set successfully! Please login.')
      navigate({ to: '/sign-in' })
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Token expired or invalid. Please request a new invite.')
    } finally {
      setIsLoading(false)
    }
  }

  if (!token) {
    return (
      <div className='min-h-screen flex items-center justify-center bg-[#0B1829]'>
        <div className='bg-white rounded-xl p-8 max-w-md w-full mx-4 text-center'>
          <h2 className='text-xl font-semibold text-red-600 mb-2'>Invalid Link</h2>
          <p className='text-gray-500'>This invite link is invalid or has expired.</p>
          <p className='text-gray-500 mt-1'>Please contact your admin for a new invite.</p>
        </div>
      </div>
    )
  }

  return (
    <div className='min-h-screen flex items-center justify-center bg-[#0B1829]'>
      <div className='bg-white rounded-xl p-8 max-w-md w-full mx-4'>
        <div className='text-center mb-6'>
          <h1 className='text-2xl font-bold text-foreground'>Set Your Password</h1>
          <p className='text-gray-500 text-sm mt-1'>
            Welcome to BBC Admin Panel. Set a secure password to continue.
          </p>
        </div>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className='space-y-4'>
            <FormField
              control={form.control}
              name='password'
              render={({ field }) => (
                <FormItem>
                  <FormLabel>New Password</FormLabel>
                  <FormControl>
                    <PasswordInput placeholder='Min 8 characters' {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name='confirm'
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Confirm Password</FormLabel>
                  <FormControl>
                    <PasswordInput placeholder='Repeat password' {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <Button
              type='submit'
              className='w-full bg-[#C9A54E] hover:bg-[#b8943d] text-white'
              disabled={isLoading}
            >
              {isLoading ? 'Setting password...' : 'Set Password & Login'}
            </Button>
          </form>
        </Form>
      </div>
    </div>
  )
}
