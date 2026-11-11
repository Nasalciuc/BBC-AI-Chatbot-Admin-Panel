import { LOGO_SRC } from '@/lib/brand'
import { cn } from '@/lib/utils'

/**
 * The company's logo. Where the name is written beside it the image is
 * decorative: pass alt='' there, so a screen reader does not say it twice.
 */
export function Logo({ className, ...props }: React.ImgHTMLAttributes<HTMLImageElement>) {
  return <img src={LOGO_SRC} alt='BBC Logo' className={cn('size-6', className)} {...props} />
}
