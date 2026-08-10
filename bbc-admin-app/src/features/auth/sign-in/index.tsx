import { useSearch } from '@tanstack/react-router'
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { AuthLayout } from '../auth-layout'
import { UserAuthForm } from './components/user-auth-form'

export function SignIn() {
  const { redirect } = useSearch({ from: '/(auth)/sign-in' })

  return (
    <AuthLayout>
      <Card className='gap-4'>
        <CardHeader>
          <CardTitle className='text-lg tracking-tight'>Sign in</CardTitle>
          <CardDescription>
            Enter your email and password below to <br />
            log into your account
          </CardDescription>
        </CardHeader>
        <CardContent>
          <UserAuthForm redirectTo={redirect} />
        </CardContent>
        <CardFooter>
          {/* No dead /terms and /privacy links — this is an internal ops
              panel; access questions go to the administrator. */}
          <p className='px-8 text-center text-sm text-muted-foreground'>
            Internal panel for the BuyBusinessClass team. Need access? Ask
            your administrator.
          </p>
        </CardFooter>
      </Card>
    </AuthLayout>
  )
}
