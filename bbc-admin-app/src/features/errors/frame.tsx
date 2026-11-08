import { cn } from '@/lib/utils'

/** A whole-page message: code and kicker, a big title, what happened, and
 *  what to do. Dark for "the panel is being updated". */
export function ErrorFrame({
  kicker,
  title,
  text,
  dark = false,
  children,
}: {
  kicker: string
  title: string
  text: React.ReactNode
  dark?: boolean
  children?: React.ReactNode
}) {
  return (
    <main
      id='content'
      className={cn('flex min-h-svh items-center justify-center px-6 py-16', dark ? 'bg-nav text-nav-active-fg' : 'bg-k-ffffff text-k-202020')}
    >
      <div className='flex w-full max-w-[560px] flex-col gap-4'>
        <p className={cn('m-0 text-[13px] font-bold tracking-[0.06em] uppercase', dark ? 'text-nav-sub' : 'text-k-555555')}>{kicker}</p>
        <h1 className='m-0 font-display text-[36px] leading-[1.1] font-bold tracking-[-0.01em] md:text-[44px]'>{title}</h1>
        <p className={cn('m-0 text-base leading-[1.55]', dark ? 'text-nav-sub' : 'text-k-444444')}>{text}</p>
        {children}
      </div>
    </main>
  )
}
