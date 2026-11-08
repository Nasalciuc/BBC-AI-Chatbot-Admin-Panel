import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { type ApiError, inviteUser } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { ROLE_LABEL, ROLE_ORDER, ROLE_TEXT, emailTypo, tunnelFor, validEmail, type Role } from '../view'

const field = 'h-11 w-full rounded-xl border border-k-d9d9d9 bg-k-ffffff px-3.5 text-sm text-k-202020 focus-visible:outline-2 focus-visible:outline-k-6ea8ff'

export function InviteDialog({ open, onOpenChange, myRole }: { open: boolean; onOpenChange: (o: boolean) => void; myRole: string }) {
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<Role>('sales')
  const [tried, setTried] = useState(false)
  const typo = emailTypo(email)
  const roles = ROLE_ORDER.filter((r) => r !== 'owner' || myRole === 'owner')
  const nameOk = name.trim().length >= 2
  const emailOk = validEmail(email)

  const reset = () => {
    setName('')
    setEmail('')
    setRole('sales')
    setTried(false)
  }
  const send = useMutation({
    mutationFn: () => inviteUser({ name: name.trim(), email: email.trim().toLowerCase(), role, tunnel_scope: tunnelFor(role) }),
    onSuccess: (r) => {
      const d = r.data as { email_sent?: boolean; reactivated?: boolean; reinvited?: boolean }
      const who = name.trim() || email.trim()
      if (d.email_sent === false) toast.warning(`${who}’s account is ready, but the e-mail didn’t go out. Use Resend invite.`)
      else if (d.reactivated) toast.success(`${who}’s account is back on. The invite sets a new password.`)
      else toast.success(`Invite sent to ${email.trim().toLowerCase()}. The link works for 48 hours.`)
      qc.invalidateQueries({ queryKey: ['panel-users'] })
      qc.invalidateQueries({ queryKey: ['access-log'] })
      reset()
      onOpenChange(false)
    },
    onError: (e) => toast.error((e as ApiError)?.message ?? 'Couldn’t send the invite. Try again.'),
  })

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) reset()
        onOpenChange(o)
      }}
    >
      <DialogContent className='max-h-[92vh] gap-4 overflow-y-auto rounded-[24px] bg-k-ffffff p-6 sm:max-w-[600px]'>
        <DialogTitle className='m-0 text-[22px] font-bold text-k-202020'>Invite someone</DialogTitle>
        <DialogDescription className='sr-only'>Name, work e-mail and role. They get a link to set their password.</DialogDescription>
        <form
          className='flex flex-col gap-4'
          onSubmit={(e) => {
            e.preventDefault()
            setTried(true)
            if (nameOk && emailOk && !send.isPending) send.mutate()
          }}
        >
          <div className='grid grid-cols-1 gap-3 sm:grid-cols-2'>
            <label className='flex flex-col gap-1.5 text-sm font-bold text-k-202020'>
              Full name
              <input value={name} onChange={(e) => setName(e.target.value)} autoComplete='off' className={field} aria-invalid={tried && !nameOk} />
              {tried && !nameOk && <span className='text-xs font-normal text-k-b3261e'>Write their name (at least 2 letters).</span>}
            </label>
            <label className='flex flex-col gap-1.5 text-sm font-bold text-k-202020'>
              Work e-mail
              <input
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                type='email'
                autoComplete='off'
                className={field}
                aria-invalid={tried && !emailOk}
              />
              {tried && !emailOk && <span className='text-xs font-normal text-k-b3261e'>That doesn’t look like an e-mail address.</span>}
            </label>
          </div>
          {typo && (
            <div role='status' className='flex flex-wrap items-center gap-3 rounded-2xl bg-k-fff1dc px-3.5 py-3 text-[13px] text-k-8a4b00'>
              <span className='min-w-0 flex-1'>
                “{typo.domain}” looks like a typo of {typo.fixed}. The invite would bounce.
              </span>
              <button
                type='button'
                onClick={() => setEmail(typo.suggestion)}
                className='h-9 rounded-full border border-k-d9d9d9 bg-k-ffffff px-3.5 text-xs font-bold text-k-202020'
              >
                Use {typo.suggestion}
              </button>
            </div>
          )}
          <fieldset className='m-0 flex flex-col gap-2 border-0 p-0'>
            <legend className='mb-2 text-sm font-bold text-k-202020'>Role</legend>
            {roles.map((r) => (
              <label
                key={r}
                className={cn(
                  'flex cursor-pointer items-start gap-3 rounded-2xl border px-4 py-3',
                  role === r ? 'border-kl-202020 bg-k-f7f7f7' : 'border-k-d9d9d9'
                )}
              >
                <input type='radio' name='invite-role' value={r} checked={role === r} onChange={() => setRole(r)} className='mt-1 accent-current' />
                <span className='flex flex-col'>
                  <strong className='text-sm text-k-202020'>{ROLE_LABEL[r]}</strong>
                  <span className='text-[13px] text-k-555555'>{ROLE_TEXT[r]}</span>
                </span>
              </label>
            ))}
          </fieldset>
          <div className='flex flex-wrap items-center gap-3'>
            <p className='m-0 min-w-0 flex-1 text-xs text-k-6b6b6b'>
              The link in the e-mail works for 48 hours. If they were deactivated before, the invite turns their account back on.
            </p>
            <button type='submit' disabled={send.isPending} className='h-11 rounded-full bg-kb-202020 px-6 text-[15px] font-bold text-on-btn disabled:opacity-60'>
              {send.isPending ? 'Sending…' : 'Send invite'}
            </button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
