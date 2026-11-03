import { useRef, useState } from 'react'
import { z } from 'zod'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { Link, useNavigate } from '@tanstack/react-router'
import { Loader2, LogIn } from 'lucide-react'
import { toast } from 'sonner'
import { useAuthStore } from '@/stores/auth-store'
import { loginUser, type LoginUser } from '@/lib/api'
import { MfaStep } from './mfa-step'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { PasswordInput } from '@/components/password-input'

const formSchema = z.object({
  email: z.string().min(1, 'Please enter your email').email('Invalid email address'),
  password: z
    .string()
    .min(1, 'Please enter your password')
    .min(7, 'Password must be at least 7 characters long'),
})

interface UserAuthFormProps extends React.HTMLAttributes<HTMLFormElement> {
  redirectTo?: string
}

export function UserAuthForm({
  className,
  redirectTo,
  ...props
}: UserAuthFormProps) {
  const [isLoading, setIsLoading] = useState(false)
  // Honeypot: a hidden field a human never sees. A filled value means a bot.
  const honeypotRef = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()
  const { auth } = useAuthStore()

  const form = useForm<z.infer<typeof formSchema>>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      email: '',
      password: '',
    },
  })

  // PR-13: the privileged roles finish signing in with a second factor.
  const [mfa, setMfa] = useState<{ mode: 'required' | 'enroll'; token: string } | null>(null)

  function finishLogin(token: string, user: LoginUser) {
    auth.setUser({
      accountNo: user.id,
      email: user.email,
      name: user.name || '',
      role: user.role || 'sales',
      tunnelScope: user.tunnel_scope || '',
      exp: Date.now() + 24 * 60 * 60 * 1000,
      avatar_url: user.avatar_url ?? null,
      phone: user.phone ?? '',
    })
    auth.setAccessToken(token)
    toast.success(`Welcome back, ${user.name || user.email}!`)
    navigate({ to: redirectTo || '/', replace: true })
  }

  async function onSubmit(data: z.infer<typeof formSchema>) {
    setIsLoading(true)
    try {
      const res = await loginUser(
        data.email,
        data.password,
        honeypotRef.current?.value || undefined
      )
      if (res.mfa && res.mfa_token) {
        setMfa({ mode: res.mfa, token: res.mfa_token })
        return
      }
      finishLogin(res.token, res.user)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Login failed'
      toast.error(message)
    } finally {
      setIsLoading(false)
    }
  }

  if (mfa) {
    return (
      <MfaStep
        mode={mfa.mode}
        mfaToken={mfa.token}
        onDone={finishLogin}
        onCancel={() => { setMfa(null); form.reset({ email: form.getValues('email'), password: '' }) }}
      />
    )
  }

  return (
    <Form {...form}>
      <form
        onSubmit={form.handleSubmit(onSubmit)}
        className={cn('grid gap-3', className)}
        {...props}
      >
        {/* Honeypot — hidden from people and assistive tech, a bait for bots. */}
        <input
          ref={honeypotRef}
          type='text'
          name='company_fax'
          tabIndex={-1}
          autoComplete='off'
          aria-hidden='true'
          style={{ position: 'absolute', left: '-9999px', width: '1px', height: '1px', opacity: 0 }}
        />
        <FormField
          control={form.control}
          name='email'
          render={({ field }) => (
            <FormItem>
              <FormLabel>Email</FormLabel>
              <FormControl>
                <Input placeholder='name@example.com' {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name='password'
          render={({ field }) => (
            <FormItem className='relative'>
              <FormLabel>Password</FormLabel>
              <FormControl>
                <PasswordInput placeholder='********' {...field} />
              </FormControl>
              <FormMessage />
              <Link
                to='/forgot-password'
                className='absolute end-0 -top-0.5 text-sm font-medium text-muted-foreground hover:opacity-75'
              >
                Forgot password?
              </Link>
            </FormItem>
          )}
        />
        <Button className='mt-2' disabled={isLoading}>
          {isLoading ? <Loader2 className='animate-spin' /> : <LogIn />}
          Sign in
        </Button>
      </form>
    </Form>
  )
}
