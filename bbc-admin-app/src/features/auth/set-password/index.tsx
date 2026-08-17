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


type InvalidCardProps = {
  title: string
  body: string
  allowRequest: boolean
  email: string
  onEmailChange: (value: string) => void
  onRequest: () => void
  requesting: boolean
}

// Declared at MODULE level on purpose: nested inside the page component it
// was a brand-new component type on every render, so React unmounted and
// remounted the input and the operator lost focus after each keystroke.
function InvalidCard({
  title, body, allowRequest, email, onEmailChange, onRequest, requesting,
}: InvalidCardProps) {
  return (
    <div className='min-h-screen flex items-center justify-center bg-[#0B1829]'>
      <div className='bg-white rounded-xl p-8 max-w-md w-full mx-4 text-center'>
        <h2 className='text-xl font-semibold text-red-600 mb-2'>{title}</h2>
        <p className='text-gray-500'>{body}</p>
        {allowRequest ? (
          <div className='mt-5 space-y-2 text-left'>
            <label className='text-xs font-medium text-gray-600' htmlFor='reinvite-email'>
              Your work email
            </label>
            <input
              id='reinvite-email'
              type='email'
              value={email}
              onChange={(e) => onEmailChange(e.target.value)}
              placeholder='you@buybusinessclass.com'
              className='w-full px-3 py-2 text-sm rounded-lg border border-input bg-background text-foreground'
            />
            <button
              type='button'
              onClick={onRequest}
              disabled={requesting || !email.trim()}
              className='w-full py-2 rounded-lg bg-[#C9A54E] text-white text-sm font-semibold hover:bg-[#C9A54E]/90 disabled:opacity-50'
            >
              {requesting ? 'Sending…' : 'Request a new invite link'}
            </button>
          </div>
        ) : (
          <p className='text-gray-500 mt-1'>Please contact your admin.</p>
        )}
      </div>
    </div>
  )
}

export default function SetPasswordPage() {
  const navigate = useNavigate()
  const search = useSearch({ strict: false }) as { token?: string }
  const token = search?.token ?? ''
  const [isLoading, setIsLoading] = useState(false)
  const [failure, setFailure] = useState<{ reason: string; message: string } | null>(null)
  const [email, setEmail] = useState('')
  const [requesting, setRequesting] = useState(false)

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
      // The backend now says WHY (expired / used / superseded / unknown).
      // "Expired or invalid" for all four made every failure read like the
      // operator's mistake — and only some of them are worth re-issuing.
      const raw = err instanceof Error ? err.message : ''
      const reason = raw.split(':')[0]?.trim()
      if (['expired', 'used', 'superseded', 'unknown'].includes(reason)) {
        setFailure({ reason, message: raw.slice(raw.indexOf(':') + 1).trim() })
      } else {
        toast.error(raw || 'Token expired or invalid. Please request a new invite.')
      }
    } finally {
      setIsLoading(false)
    }
  }

  const requestNewLink = async () => {
    if (!email.trim() || requesting) return
    setRequesting(true)
    try {
      await apiFetch('/api/auth/request-invite', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email.trim() }),
      })
      // Deliberately the same answer either way — this page must not
      // become a way to discover which addresses have accounts.
      toast.success('If that address has a pending invite, a new link is on its way.')
    } catch {
      toast.error("Couldn't request a new link — please contact your admin.")
    } finally {
      setRequesting(false)
    }
  }

  if (failure) {
    const titles: Record<string, string> = {
      expired: 'Link expired',
      used: 'Already used',
      superseded: 'A newer invite was sent',
      unknown: 'Invalid link',
    }
    return (
      <InvalidCard
        title={titles[failure.reason] ?? 'Invalid link'}
        body={failure.message}
        allowRequest={failure.reason === 'expired' || failure.reason === 'unknown'}
        email={email}
        onEmailChange={setEmail}
        onRequest={requestNewLink}
        requesting={requesting}
      />
    )
  }

  if (!token) {
    return (
      <InvalidCard
        title='Invalid Link'
        body='This invite link is invalid or has expired.'
        allowRequest
        email={email}
        onEmailChange={setEmail}
        onRequest={requestNewLink}
        requesting={requesting}
      />
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
