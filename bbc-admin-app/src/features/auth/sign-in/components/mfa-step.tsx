import { useEffect, useState } from 'react'
import {
  confirmMfaEnroll, startMfaEnroll, verifyMfa,
  type LoginResult, type LoginUser,
} from '@/lib/api'
import { cn } from '@/lib/utils'
import { AuthNote, AuthTitle, Field } from '../../shell'
import { inputCls, linkCls, primaryBtn } from '../../styles'

/**
 * The second step of a privileged sign-in (PR-13), in the sign-in's own look.
 *  - mode "required": a 6-digit code from the authenticator app, or one of
 *    the recovery codes;
 *  - mode "enroll": first sign-in since 2FA became required — scan the QR,
 *    confirm one code, keep the 10 recovery codes (shown once).
 * The short-lived mfa_token is never the session: only the token onDone gets.
 */
export function MfaStep({
  mode, mfaToken, onDone, onCancel,
}: {
  mode: 'required' | 'enroll'
  mfaToken: string
  onDone: (token: string, user: LoginUser) => void
  onCancel: () => void
}) {
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [qr, setQr] = useState<{ otpauth_uri: string; qr_svg: string } | null>(null)
  const [recovery, setRecovery] = useState<{ codes: string[]; token: string; user: LoginUser } | null>(null)
  // A failed code is said under the field; a set-up that couldn't start is
  // said above the form, and ends the waiting square.
  const [error, setError] = useState<string | null>(null)
  const [setupFailed, setSetupFailed] = useState(false)

  useEffect(() => {
    if (mode !== 'enroll') return
    let alive = true
    startMfaEnroll(mfaToken)
      .then((d) => { if (alive) setQr(d) })
      .catch((e: unknown) => {
        if (!alive) return
        setSetupFailed(true)
        setError(e instanceof Error ? e.message : 'Could not start the set-up')
      })
    return () => { alive = false }
  }, [mode, mfaToken])

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (busy || !code.trim()) return
    setBusy(true)
    setError(null)
    try {
      if (mode === 'required') {
        const r: LoginResult = await verifyMfa(mfaToken, code.trim())
        onDone(r.token, r.user)
      } else {
        const r = await confirmMfaEnroll(mfaToken, code.trim())
        if (r.token && r.user) setRecovery({ codes: r.recovery_codes, token: r.token, user: r.user })
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'That code did not work')
      setCode('')
    } finally {
      setBusy(false)
    }
  }

  if (recovery) {
    return (
      <>
        <AuthTitle
          title='Two-step sign-in is on'
          text='Keep these recovery codes somewhere safe. Each works once, if you lose your phone. They will not be shown again.'
        />
        <ul className='m-0 grid list-none grid-cols-2 gap-1.5 rounded-[14px] bg-k-f7f7f7 p-4 font-mono text-[15px] text-k-202020'>
          {recovery.codes.map((c) => <li key={c}>{c}</li>)}
        </ul>
        <button
          type='button'
          data-testid='mfa-recovery-done'
          onClick={() => onDone(recovery.token, recovery.user)}
          className={primaryBtn()}
        >
          I have saved them — continue
        </button>
      </>
    )
  }

  const codeErr = error && !setupFailed ? error : ''
  return (
    <>
      <AuthTitle
        title={mode === 'enroll' ? 'Set up two-step sign-in' : 'Two-step sign-in'}
        text={
          mode === 'enroll'
            ? 'Your role now needs a second step at sign-in. Scan this code with an authenticator app (Google Authenticator, 1Password, Authy…), then enter the 6 digits it shows.'
            : 'Enter the 6-digit code from your authenticator app, or one of your recovery codes.'
        }
      />
      {setupFailed && error && <AuthNote tone='bad' head='Couldn’t start the set-up.' text={error} />}
      <form noValidate onSubmit={submit} className='flex flex-col gap-4'>
        {mode === 'enroll' && (
          qr ? (
            <div className='flex flex-col items-center gap-2'>
              {/* An image, never injected markup: an <img> SVG cannot run script.
                  Light behind the code in both themes, so a phone can read it. */}
              <img
                alt='QR code for your authenticator app'
                className='size-48 rounded-[14px] bg-nav-active-fg p-2'
                src={`data:image/svg+xml;utf8,${encodeURIComponent(qr.qr_svg)}`}
              />
              <a className={cn(linkCls, 'text-[13px]')} href={qr.otpauth_uri}>
                On this device? Open in your authenticator app
              </a>
            </div>
          ) : setupFailed ? null : (
            <div aria-hidden='true' className='mx-auto size-48 animate-pulse rounded-[14px] bg-k-f2f2f2' />
          )
        )}
        <Field id='mfa-code' label={mode === 'enroll' ? 'Code from the app' : 'Code'} error={codeErr}>
          <input
            id='mfa-code'
            autoFocus
            inputMode={mode === 'enroll' ? 'numeric' : 'text'}
            autoComplete='one-time-code'
            placeholder={mode === 'enroll' ? '123456' : '123456 or abcde-fghjk'}
            value={code}
            onChange={(e) => { setCode(e.target.value); if (codeErr) setError(null) }}
            aria-invalid={!!codeErr}
            aria-describedby={codeErr ? 'mfa-code-error' : undefined}
            data-testid='mfa-code'
            className={cn(inputCls, codeErr ? 'border-k-b3261e' : 'border-k-d9d9d9')}
          />
        </Field>
        <button type='submit' disabled={busy || (mode === 'enroll' && !qr)} data-testid='mfa-submit' className={primaryBtn('mt-1')}>
          {busy ? 'Checking…' : mode === 'enroll' ? 'Turn on and sign in' : 'Verify'}
        </button>
      </form>
      <button type='button' onClick={onCancel} className={cn(linkCls, 'self-start text-sm')}>
        Back to sign in
      </button>
    </>
  )
}
