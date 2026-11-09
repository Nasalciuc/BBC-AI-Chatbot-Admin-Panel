import { cn } from '@/lib/utils'

/** A Settings card: white with a hairline, or grey for what you can't change. */
export function SettingsCard({
  title,
  aside,
  text,
  tone = 'white',
  label,
  children,
}: {
  title: string
  aside?: React.ReactNode
  text?: React.ReactNode
  tone?: 'white' | 'grey'
  label?: string
  children?: React.ReactNode
}) {
  return (
    <section
      aria-label={label ?? title}
      className={cn('flex flex-col gap-4 rounded-[20px] p-5 md:px-[22px]', tone === 'grey' ? 'bg-k-f7f7f7' : 'bg-k-ffffff ring-1 ring-k-ececec')}
    >
      <div className='flex flex-col gap-1'>
        <h2 className='m-0 flex flex-wrap items-baseline gap-x-2.5 text-lg font-bold text-k-202020'>
          {title}
          {aside && <span className='text-[13px] font-normal text-k-555555'>{aside}</span>}
        </h2>
        {text && <p className='m-0 text-sm leading-[1.5] text-k-555555'>{text}</p>}
      </div>
      {children}
    </section>
  )
}
