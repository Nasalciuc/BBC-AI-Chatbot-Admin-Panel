import { Link } from '@tanstack/react-router'
import { AuthShell, AuthTitle } from '../shell'
import { linkCls, primaryBtn } from '../styles'

/** There is no password reset to send: the page says who resets it. */
export function ForgotPassword() {
  return (
    <AuthShell>
      <AuthTitle title='Forgot your password?' text='Ask the owner or an administrator to reset it for you.' />
      <p className='m-0 text-[15px] leading-[1.5] text-k-555555'>
        Never set a password? Open the link in your invite e-mail, or{' '}
        <Link to='/get-link' className={linkCls}>
          get a new link
        </Link>
        .
      </p>
      <Link to='/sign-in' className={primaryBtn()}>
        Back to sign in
      </Link>
    </AuthShell>
  )
}
