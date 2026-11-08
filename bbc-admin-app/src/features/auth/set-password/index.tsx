import { useState } from 'react'
import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { ApiError, setPasswordFromInvite } from '@/lib/api'
import { cn } from '@/lib/utils'
import { AuthNote, AuthShell, AuthTitle, PasswordField } from '../shell'
import { linkCls, primaryBtn } from '../styles'
import { LINK_TEXT, linkProblem, passwordRules, type LinkProblem } from '../view'

/**
 * Choose a password from an invite link (POST /api/auth/set-password). When
 * the link can't be used, the page says why: expired, already used, a newer
 * invite was sent, or not a link at all.
 */
export default function SetPasswordPage() {
  const search = useSearch({ from: '/(auth)/set-password' })
  const navigate = useNavigate()
  const token = search.token ?? ''
  const [pw, setPw] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [link, setLink] = useState<LinkProblem | null>(token ? null : 'unknown')
  const [done, setDone] = useState(false)
  const rules = passwordRules(pw)

  const save = async () => {
    if (busy) return
    if (!rules.every((r) => r.ok)) {
      setErr(pw ? 'The password doesn’t meet the rule above yet.' : 'Type a new password.')
      return
    }
    setBusy(true)
    setErr('')
    try {
      await setPasswordFromInvite(token, pw)
      setDone(true)
    } catch (e) {
      const status = e instanceof ApiError ? e.status : null
      // 400 names the reason; 422 is the link itself (the server wants 20 to
      // 512 characters — a link cut short in the e-mail), since the password
      // already passed the same 8–128 rule here.
      const reason = status === 400 ? linkProblem(e instanceof Error ? e.message : '') : status === 422 ? 'unknown' : null
      if (reason) setLink(reason)
      else if (status === 429) setErr('Too many tries. Wait a minute and try again.')
      else if (status === null) setErr('Can’t reach the server. Check your connection and try again.')
      else setErr('Couldn’t save it. Try again, or ask your administrator.')
    } finally {
      setBusy(false)
    }
  }

  if (done) {
    return (
      <AuthShell>
        <span aria-hidden='true' className='flex size-12 items-center justify-center rounded-full bg-k-e3f6ea text-xl font-bold text-k-0a6b35'>
          ✓
        </span>
        <AuthTitle title='Your password is saved' text='Use it with the e-mail your invite came to. Your browser or password manager can remember it.' />
        <button type='button' onClick={() => navigate({ to: '/sign-in' })} className={primaryBtn()}>
          Sign in
        </button>
      </AuthShell>
    )
  }

  if (link) {
    const t = LINK_TEXT[link]
    return (
      <AuthShell>
        <AuthTitle title={t.head} text={t.text} />
        {t.action === 'new_link' ? (
          <>
            <Link to='/get-link' search={{ why: link === 'expired' ? 'expired' : 'lost' }} className={primaryBtn()}>
              Get a new link
            </Link>
            <p className='m-0 text-sm text-k-555555'>
              Already set a password?{' '}
              <Link to='/sign-in' className={linkCls}>
                Sign in
              </Link>
              .
            </p>
          </>
        ) : (
          <Link to='/sign-in' className={primaryBtn()}>
            Sign in
          </Link>
        )}
      </AuthShell>
    )
  }

  return (
    <AuthShell>
      <AuthTitle kicker='Welcome to the chat panel' title='Choose your password' text='You’ll sign in with it and the e-mail your invite came to.' />
      {err && <AuthNote tone='bad' head={err} />}
      <form
        noValidate
        onSubmit={(e) => {
          e.preventDefault()
          void save()
        }}
        className='flex flex-col gap-4'
      >
        <PasswordField
          id='new-password'
          label='New password'
          value={pw}
          onChange={(v) => {
            setPw(v)
            setErr('')
          }}
          autoComplete='new-password'
        />
        <ul className='m-0 flex list-none flex-col gap-1 p-0'>
          {rules.map((r) => (
            <li key={r.text} className={cn('flex items-center gap-2 text-sm', r.ok ? 'text-k-0a6b35' : 'text-k-555555')}>
              <span
                aria-hidden='true'
                className={cn('flex size-4 items-center justify-center rounded-full text-[11px] font-bold', r.ok ? 'bg-k-00a645 text-on-btn' : 'bg-k-e9e9e9')}
              >
                {r.ok ? '✓' : ''}
              </span>
              {r.text}
              <span className='sr-only'>{r.ok ? ' (done)' : ' (not yet)'}</span>
            </li>
          ))}
        </ul>
        <button type='submit' disabled={busy} className={primaryBtn()}>
          {busy ? 'Saving…' : 'Save my password'}
        </button>
      </form>
    </AuthShell>
  )
}
