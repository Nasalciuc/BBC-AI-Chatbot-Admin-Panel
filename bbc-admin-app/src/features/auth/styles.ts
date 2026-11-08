import { cn } from '@/lib/utils'

// The sign-in screens' shared classes (kept out of shell.tsx for fast refresh).

export const inputCls =
  'h-[50px] w-full rounded-[14px] border bg-k-ffffff px-4 text-[16px] text-k-202020 placeholder:text-k-8a8a8a focus-visible:outline-2 focus-visible:outline-k-6ea8ff'

export function primaryBtn(extra?: string) {
  return cn(
    'flex h-[50px] w-full items-center justify-center rounded-full bg-kb-202020 text-[16px] font-bold text-on-btn focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-k-6ea8ff disabled:opacity-60',
    extra
  )
}

export const linkCls = 'font-bold text-k-5b45e0 hover:underline focus-visible:outline-2 focus-visible:outline-k-6ea8ff'
