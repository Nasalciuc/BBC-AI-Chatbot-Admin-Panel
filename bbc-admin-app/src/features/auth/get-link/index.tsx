import { useState } from 'react'
import { Link, useSearch } from '@tanstack/react-router'
import { ApiError, requestInviteLink } from '@/lib/api'
import { cn } from '@/lib/utils'
import { AuthNote, AuthShell, AuthTitle, Field } from '../shell'
import { inputCls, linkCls, primaryBtn } from '../styles'
import { emailError } from '../view'

/**
 * A new invite link (POST /api/auth/request-invite). The server answers the
 * same whether or not the address has an account, and sends a link only to
 * accounts still waiting for a password; one try per address in 10 minutes.
 */
export function GetLink() {
  const search = useSearch({ from: '/(auth)/get-link' })
  const [email, setEmail] = useState(search.email ?? '')
  const [err, setErr] = useState('')
  const [sent, setSent] = useState<string | null>(null)
  const [failed, setFailed] = useState<'busy' | 'down' | null>(null)
  const [busy, setBusy] = useState(false)
  const expired = search.why === 'expired'

  const send = async () => {
    const e = emailError(email)
    setErr(e)
    if (e || busy) return
    setBusy(true)
    setFailed(null)
    try {
      await requestInviteLink(email.trim())
      setSent(email.trim())
    } catch (e) {
      setFailed(e instanceof ApiError && e.status === 429 ? 'busy' : 'down')
    } finally {
      setBusy(false)
    }
  }

  if (sent) {
    return (
      <AuthShell>
        <AuthTitle
          title='Check your e-mail'
          text={`If ${sent} has an invite waiting, a new link is on its way. It works for 48 hours, and older links stop working.`}
        />
        <p className='m-0 text-[15px] leading-[1.5] text-k-555555'>
          Nothing after a few minutes? Look in spam, or ask your administrator. You can ask again in 10 minutes.
        </p>
        <Link to='/sign-in' className={primaryBtn()}>
          Back to sign in
        </Link>
      </AuthShell>
    )
  }

  return (
    <AuthShell>
      <AuthTitle
        title='Get a new invite link'
        text='Type the e-mail your invite came to and we’ll send a fresh link. It works for 48 hours, and older links stop working.'
      />
      {expired && !failed && <AuthNote tone='warn' head='Your link expired.' text='Invite links work for 48 hours.' />}
      {failed === 'busy' && <AuthNote tone='bad' head='Too many tries.' text='Wait a minute and try again.' />}
      {failed === 'down' && <AuthNote tone='bad' head='Couldn’t ask for a link.' text='The server didn’t answer. Try again, or ask your administrator.' />}
      <form
        noValidate
        onSubmit={(e) => {
          e.preventDefault()
          void send()
        }}
        className='flex flex-col gap-4'
      >
        <Field id='invite-email' label='The e-mail your invite came to' error={err}>
          <input
            id='invite-email'
            type='email'
            autoComplete='email'
            value={email}
            aria-invalid={!!err}
            aria-describedby={err ? 'invite-email-error' : undefined}
            onChange={(e) => {
              setEmail(e.target.value)
              setErr('')
            }}
            className={cn(inputCls, err ? 'border-k-b3261e' : 'border-k-d9d9d9')}
          />
        </Field>
        <button type='submit' disabled={busy} className={primaryBtn()}>
          {busy ? 'Sending…' : 'Send me a new link'}
        </button>
      </form>
      <p className='m-0 text-sm text-k-555555'>
        Already set a password?{' '}
        <Link to='/sign-in' className={linkCls}>
          Sign in
        </Link>
        .
      </p>
    </AuthShell>
  )
}
