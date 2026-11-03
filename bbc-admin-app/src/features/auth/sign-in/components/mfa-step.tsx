import { useEffect, useState } from 'react'
import { Loader2, ShieldCheck } from 'lucide-react'
import { toast } from 'sonner'
import {
  confirmMfaEnroll, startMfaEnroll, verifyMfa,
  type LoginResult, type LoginUser,
} from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

/**
 * The second step of a privileged sign-in (PR-13).
 *  - mode "required": a 6-digit code from the authenticator app, or one of
 *    the recovery codes;
 *  - mode "enroll": first sign-in since 2FA became required — scan the QR,
 *    confirm one code, keep the 10 recovery codes (shown once).
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

  useEffect(() => {
    if (mode !== 'enroll') return
    let alive = true
    startMfaEnroll(mfaToken)
      .then((d) => { if (alive) setQr(d) })
      .catch((e: unknown) => toast.error(e instanceof Error ? e.message : 'Could not start the set-up'))
    return () => { alive = false }
  }, [mode, mfaToken])

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!code.trim()) return
    setBusy(true)
    try {
      if (mode === 'required') {
        const r: LoginResult = await verifyMfa(mfaToken, code.trim())
        onDone(r.token, r.user)
      } else {
        const r = await confirmMfaEnroll(mfaToken, code.trim())
        if (r.token && r.user) setRecovery({ codes: r.recovery_codes, token: r.token, user: r.user })
      }
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'That code did not work')
      setCode('')
    } finally {
      setBusy(false)
    }
  }

  if (recovery) {
    return (
      <div className='grid gap-3'>
        <div className='flex items-center gap-2 font-medium text-foreground'>
          <ShieldCheck className='h-4 w-4' /> Two-factor sign-in is on
        </div>
        <p className='text-sm text-muted-foreground'>
          Keep these recovery codes somewhere safe. Each works once, if you lose your phone.
          They will not be shown again.
        </p>
        <ul className='grid grid-cols-2 gap-1 rounded-md border border-border p-3 font-mono text-sm text-foreground'>
          {recovery.codes.map((c) => <li key={c}>{c}</li>)}
        </ul>
        <Button
          type='button'
          data-testid='mfa-recovery-done'
          onClick={() => onDone(recovery.token, recovery.user)}
        >
          I have saved them — continue
        </Button>
      </div>
    )
  }

  return (
    <form onSubmit={submit} className='grid gap-3'>
      {mode === 'enroll' ? (
        <>
          <p className='text-sm text-muted-foreground'>
            Your role now needs a second step at sign-in. Scan this code with an authenticator app
            (Google Authenticator, 1Password, Authy…), then enter the 6 digits it shows.
          </p>
          {qr ? (
            <img
              alt='QR code for your authenticator app'
              className='mx-auto h-48 w-48 rounded-md bg-background'
              src={`data:image/svg+xml;utf8,${encodeURIComponent(qr.qr_svg)}`}
            />
          ) : (
            <div className='mx-auto flex h-48 w-48 items-center justify-center'>
              <Loader2 className='animate-spin text-muted-foreground' />
            </div>
          )}
          {qr && (
            <a className='text-center text-xs text-muted-foreground underline' href={qr.otpauth_uri}>
              On this device? Open in your authenticator app
            </a>
          )}
        </>
      ) : (
        <p className='text-sm text-muted-foreground'>
          Enter the 6-digit code from your authenticator app, or one of your recovery codes.
        </p>
      )}
      <Input
        autoFocus
        inputMode={mode === 'enroll' ? 'numeric' : 'text'}
        autoComplete='one-time-code'
        placeholder={mode === 'enroll' ? '123456' : '123456 or abcde-fghjk'}
        value={code}
        onChange={(e) => setCode(e.target.value)}
        data-testid='mfa-code'
      />
      <Button disabled={busy || (mode === 'enroll' && !qr)} data-testid='mfa-submit'>
        {busy ? <Loader2 className='animate-spin' /> : <ShieldCheck />}
        {mode === 'enroll' ? 'Turn on and sign in' : 'Verify'}
      </Button>
      <Button type='button' variant='ghost' onClick={onCancel}>Back</Button>
    </form>
  )
}
