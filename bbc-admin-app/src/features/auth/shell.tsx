import { useState } from 'react'
import { cn } from '@/lib/utils'
import { inputCls } from './styles'

/** Sign-in, invite and password screens: the dark side with what the panel
 *  is for, and the form on the right. On a phone the dark side folds away. */
export function AuthShell({ children }: { children: React.ReactNode }) {
  return (
    <div className='flex min-h-svh bg-k-ffffff'>
      <aside className='hidden w-[560px] flex-none flex-col bg-nav p-10 text-nav-active-fg lg:flex'>
        <div>
          <p className='m-0 text-xl font-bold text-nav-active-fg'>BuyBusinessClass</p>
          <p className='m-0 text-sm text-nav-sub'>Chat panel</p>
        </div>
        <div className='mt-auto flex flex-col gap-6 pb-12'>
          <p className='m-0 max-w-[460px] font-display text-[40px] leading-[1.15] font-semibold tracking-[-0.01em] text-nav-active-fg'>
            Every chat is a trip someone is planning.
          </p>
          {/* An illustration, not data: what a good answer looks like. */}
          <div aria-hidden='true' className='flex max-w-[420px] flex-col gap-2.5'>
            <p className='m-0 max-w-[370px] rounded-[14px] bg-nav-active px-3.5 py-3 text-sm leading-[1.5] text-nav-active-fg'>
              Business class to London in March, two people. Is Thursday cheaper?
            </p>
            <p className='m-0 ms-12 rounded-[14px] bg-nav-active-fg px-3.5 py-3 text-sm leading-[1.5] text-nav'>
              Thursday is the better day: two seats left at that price. Shall I hold them while we talk?
            </p>
            <p className='m-0 flex items-center justify-end gap-1.5 text-xs text-nav-sub'>
              <span className='size-2 rounded-full bg-k-00c450' /> Ana · answered in 41 s
            </p>
          </div>
        </div>
        <p className='m-0 text-[13px] text-nav-sub'>The AI answers first; you take over when a person is needed.</p>
      </aside>
      <main className='flex min-w-0 flex-1 flex-col'>
        <div className='px-6 pt-6 lg:hidden'>
          <p className='m-0 text-lg font-bold text-k-202020'>BuyBusinessClass</p>
          <p className='m-0 text-[13px] text-k-555555'>Chat panel</p>
        </div>
        <div className='flex flex-1 items-center justify-center px-6 py-10'>
          <div className='flex w-full max-w-[400px] flex-col gap-5'>{children}</div>
        </div>
        <p className='m-0 px-6 pb-8 text-center text-[13px] text-k-555555'>
          Internal panel for the BuyBusinessClass team. Need access? Ask your administrator.
        </p>
      </main>
    </div>
  )
}

export function AuthTitle({ kicker, title, text }: { kicker?: string; title: string; text?: React.ReactNode }) {
  return (
    <div className='flex flex-col gap-2'>
      {kicker && <p className='m-0 text-[13px] font-bold text-k-555555'>{kicker}</p>}
      <h1 className='m-0 text-[40px] leading-[1.1] font-bold tracking-[-0.01em] text-k-202020'>{title}</h1>
      {text && <p className='m-0 text-[15px] leading-[1.5] text-k-555555'>{text}</p>}
    </div>
  )
}

/** A red (problem) or amber/green (notice) box above the form. */
export function AuthNote({ tone, head, text }: { tone: 'bad' | 'warn' | 'ok'; head: string; text?: React.ReactNode }) {
  const cls = { bad: 'bg-k-fdecea text-k-b3261e', warn: 'bg-k-fff1dc text-k-7a5300', ok: 'bg-k-e3f6ea text-k-0a6b35' }[tone]
  const dot = { bad: 'bg-k-d93025', warn: 'bg-k-d97a00', ok: 'bg-k-00a645' }[tone]
  return (
    <div role={tone === 'bad' ? 'alert' : 'status'} className={cn('flex gap-2.5 rounded-[14px] px-4 py-3 text-sm leading-[1.45]', cls)}>
      <span aria-hidden='true' className={cn('mt-1.5 size-2 flex-none rounded-full', dot)} />
      <span>
        <strong>{head}</strong> {text}
      </span>
    </div>
  )
}

/** A labelled field; `error` turns the border red and says why under it. */
export function Field({
  id,
  label,
  error,
  right,
  children,
}: {
  id: string
  label: string
  error?: string
  right?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className='flex flex-col gap-1.5'>
      <div className='flex items-baseline justify-between gap-2'>
        <label htmlFor={id} className='text-sm font-bold text-k-202020'>
          {label}
        </label>
        {right}
      </div>
      {children}
      {error && (
        <p id={`${id}-error`} className='m-0 text-[13px] text-k-b3261e'>
          {error}
        </p>
      )}
    </div>
  )
}

/** Password with Show/Hide and the Caps Lock warning. */
export function PasswordField({
  id,
  label,
  value,
  onChange,
  error,
  right,
  autoComplete,
  onEnter,
}: {
  id: string
  label: string
  value: string
  onChange: (v: string) => void
  error?: string
  right?: React.ReactNode
  autoComplete: 'current-password' | 'new-password'
  onEnter?: () => void
}) {
  const [show, setShow] = useState(false)
  const [caps, setCaps] = useState(false)
  return (
    <Field id={id} label={label} error={error} right={right}>
      <div className='relative'>
        <input
          id={id}
          type={show ? 'text' : 'password'}
          value={value}
          autoComplete={autoComplete}
          aria-invalid={!!error}
          aria-describedby={error ? `${id}-error` : undefined}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            setCaps(e.getModifierState?.('CapsLock') ?? false)
            if (e.key === 'Enter' && onEnter) {
              e.preventDefault()
              onEnter()
            }
          }}
          className={cn(inputCls, 'pe-20', error ? 'border-k-b3261e' : 'border-k-d9d9d9')}
        />
        <button
          type='button'
          onClick={() => setShow((v) => !v)}
          aria-label={show ? 'Hide password' : 'Show password'}
          className='absolute end-3 top-1/2 -translate-y-1/2 rounded-lg px-2 py-1 text-[13px] font-bold text-k-444444 hover:bg-k-f2f2f2'
        >
          {show ? 'Hide' : 'Show'}
        </button>
      </div>
      {caps && <p className='m-0 text-[13px] font-bold text-k-8a4b00'>Caps Lock is on.</p>}
    </Field>
  )
}
