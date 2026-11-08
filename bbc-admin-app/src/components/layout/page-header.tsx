import { cn } from '@/lib/utils'
import { SidebarTrigger } from '@/components/ui/sidebar'

/**
 * The redesigned page head: a big title, an optional line under it, and the
 * page's actions on the right. On phones the side menu is a sheet, so the
 * trigger shows there (agents on a phone get the tab bar instead — pass
 * `menuButton={false}`).
 */
export function PageHeader({
  title,
  subtitle,
  actions,
  size = 'lg',
  menuButton = true,
  className,
}: {
  title: string
  subtitle?: React.ReactNode
  actions?: React.ReactNode
  /** 'md' = 28px (Conversations), 'lg' = 40px (Your day, Knowledge…) */
  size?: 'md' | 'lg'
  menuButton?: boolean
  className?: string
}) {
  return (
    <header className={cn('flex flex-wrap items-center gap-x-3 gap-y-2.5', size === 'md' ? 'min-h-11' : 'items-end', className)}>
      {menuButton && <SidebarTrigger variant='outline' className='self-center md:hidden' />}
      <div className='flex min-w-0 flex-1 flex-col gap-1.5'>
        <h1
          className={cn(
            'm-0 font-display font-semibold text-k-202020',
            size === 'md' ? 'text-[28px] tracking-[-0.3px]' : 'text-[32px] leading-[1.1] tracking-[-0.6px] md:text-[40px]'
          )}
        >
          {title}
        </h1>
        {subtitle && <div className='text-sm text-k-555555'>{subtitle}</div>}
      </div>
      {actions}
    </header>
  )
}
