import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useRouter, useSearch } from '@tanstack/react-router'
import { ApiError, loginUser, type LoginUser } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth-store'
import { useSsoStore } from '@/stores/sso-store'
import { AuthNote, AuthShell, AuthTitle, Field, PasswordField } from '../shell'
import { inputCls, linkCls, primaryBtn } from '../styles'
import { BURST_PAUSE_S, SESSION_NOTICE, backAfterSignIn, crmProblem, emailError, problemText, signInProblem, type SignInProblem } from '../view'
import { MfaStep } from './components/mfa-step'

/**
 * Sign in. Every message is an answer the server really gives: wrong
 * details, an account with no password yet, access turned off, too many
 * tries. Opened from the CRM, it says so while the CRM sign-in runs, and
 * why if it fails.
 */
export function SignIn() {
  const search = useSearch({ from: '/(auth)/sign-in' })
  const navigate = useNavigate()
  const router = useRouter()
  const { auth } = useAuthStore()
  const sso = useSsoStore()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [emailErr, setEmailErr] = useState('')
  const [pwErr, setPwErr] = useState('')
  const [problem, setProblem] = useState<SignInProblem | null>(null)
  const [busy, setBusy] = useState(false)
  const [wait, setWait] = useState(0)
  const [ssoDismissed, setSsoDismissed] = useState(false)
  // Honeypot: a hidden field no person fills. The server counts a filled one.
  const honeypotRef = useRef<HTMLInputElement>(null)
  // The second step of a privileged sign-in (PR-13), while it is on screen.
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
    sso.done()
    // Back where you were, search included (/chats?highlight=… reopens the
    // chat). Only a place on this site; anything else goes home.
    const back = backAfterSignIn(search.redirect, window.location.origin)
    if (back) router.history.replace(back)
    else navigate({ to: '/', replace: true })
  }

  // "Too many tries": a short pause, counted down on the button.
  useEffect(() => {
    if (wait <= 0) return
    const t = setTimeout(() => setWait((w) => w - 1), 1000)
    return () => clearTimeout(t)
  }, [wait])

  const submit = async () => {
    if (busy || wait > 0) return
    const eErr = emailError(email)
    const pErr = password ? '' : 'Type your password.'
    setEmailErr(eErr)
    setPwErr(pErr)
    if (eErr || pErr) return
    setBusy(true)
    setProblem(null)
    try {
      const res = await loginUser(email.trim(), password, honeypotRef.current?.value || undefined)
      // PR-13: owner/admin/dev finish with a second factor. No session yet —
      // the short-lived mfa_token can do only that step, and is never stored.
      if (res.mfa && res.mfa_token) {
        setMfa({ mode: res.mfa, token: res.mfa_token })
        setPassword('')
        return
      }
      finishLogin(res.token, res.user)
    } catch (e) {
      const p = signInProblem(e instanceof ApiError ? e.status : null, e instanceof Error ? e.message : '')
      setProblem(p)
      setPassword('')
      if (p.kind === 'locked' && p.wait === 'seconds') setWait(BURST_PAUSE_S)
    } finally {
      setBusy(false)
    }
  }

  if (mfa) {
    return (
      <AuthShell>
        <MfaStep mode={mfa.mode} mfaToken={mfa.token} onDone={finishLogin} onCancel={() => setMfa(null)} />
      </AuthShell>
    )
  }

  if (sso.phase === 'pending' && !ssoDismissed) {
    return (
      <AuthShell>
        <AuthTitle title='Signing you in from the CRM…' text='You opened the chat panel inside the CRM, so you don’t need a password here.' />
        <div aria-hidden='true' className='h-1.5 overflow-hidden rounded-full bg-k-f2f2f2'>
          <div className='h-full w-1/3 animate-pulse rounded-full bg-kb-202020' />
        </div>
      </AuthShell>
    )
  }
  if (sso.phase === 'failed' && !ssoDismissed) {
    const p = crmProblem(sso.status, sso.detail)
    return (
      <AuthShell>
        <AuthTitle
          title={p.head}
          text={
            p.kind === 'no_account' && sso.email ? (
              <>
                The CRM knows you as <strong className='text-k-202020'>{sso.email}</strong>, and the chat panel has no account with that
                e-mail. Ask your administrator to invite that address, or sign in with the e-mail your invite came to.
              </>
            ) : (
              p.text
            )
          }
        />
        <button type='button' onClick={() => setSsoDismissed(true)} className={primaryBtn()}>
          Sign in with e-mail
        </button>
      </AuthShell>
    )
  }
  if (problem?.kind === 'off') {
    return (
      <AuthShell>
        <AuthTitle
          title='Your access is turned off'
          text='Your password is right, but an owner or admin turned off your account, so the panel won’t open. If that’s a mistake, ask your administrator to turn it back on. Nothing you did before is lost.'
        />
        <button type='button' onClick={() => setProblem(null)} className={primaryBtn()}>
          Back to sign in
        </button>
      </AuthShell>
    )
  }

  const note = problem ? problemText(problem) : null
  return (
    <AuthShell>
      <AuthTitle title='Sign in' text='Use the e-mail your invite came to.' />
      {search.session === 'ended' && !problem && <AuthNote tone='warn' head={SESSION_NOTICE.head} text={SESSION_NOTICE.text} />}
      {note && (
        <AuthNote
          tone='bad'
          head={note.head}
          text={
            problem?.kind === 'not_set_up' ? (
              <>
                {note.text}{' '}
                <Link to='/get-link' className={linkCls}>
                  Get a new link
                </Link>
                .
              </>
            ) : (
              note.text
            )
          }
        />
      )}
      <form
        noValidate
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
        className='flex flex-col gap-4'
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
        <Field id='email' label='E-mail' error={emailErr}>
          <input
            id='email'
            type='email'
            autoComplete='username'
            placeholder='name@buybusinessclass.com'
            value={email}
            aria-invalid={!!emailErr}
            aria-describedby={emailErr ? 'email-error' : undefined}
            onChange={(e) => {
              setEmail(e.target.value)
              setEmailErr('')
            }}
            className={cn(inputCls, emailErr ? 'border-k-b3261e' : 'border-k-d9d9d9')}
          />
        </Field>
        <PasswordField
          id='password'
          label='Password'
          value={password}
          onChange={(v) => {
            setPassword(v)
            setPwErr('')
          }}
          error={pwErr}
          autoComplete='current-password'
          right={
            <Link to='/forgot-password' className={cn(linkCls, 'text-[13px]')}>
              Forgot password?
            </Link>
          }
        />
        <button type='submit' disabled={busy || wait > 0} className={primaryBtn('mt-1')}>
          {wait > 0 ? `Try again in ${wait} s` : busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
      <p className='m-0 text-sm leading-[1.5] text-k-555555'>
        First time here? Open the link in your invite e-mail. Lost it?{' '}
        <Link to='/get-link' className={linkCls}>
          Get a new link
        </Link>
        .
      </p>
    </AuthShell>
  )
}
