import { toast } from 'sonner'
import { fonts } from '@/config/fonts'
import { cn } from '@/lib/utils'
import { useFont } from '@/context/font-provider'
import { useTheme } from '@/context/theme-provider'
import { SettingsCard } from '../components/card'

const THEMES = [
  { key: 'light', label: 'Light', swatch: 'bg-[#ffffff]', bar: 'bg-[#e6e6e6]' },
  { key: 'dark', label: 'Dark', swatch: 'bg-[#2a2a2a]', bar: 'bg-[#4a4a4a]' },
  { key: 'system', label: 'Same as this device', swatch: 'bg-[linear-gradient(90deg,#ffffff_50%,#2a2a2a_50%)]', bar: 'bg-[#9a9a9a]' },
] as const

const FONT_LABEL: Record<(typeof fonts)[number], string> = {
  helvetica: 'Helvetica',
  inter: 'Inter',
  display: 'System display',
  system: 'System default',
}

// Written out so Tailwind sees every class.
const FONT_CLASS: Record<(typeof fonts)[number], string> = {
  helvetica: 'font-helvetica',
  inter: 'font-inter',
  display: 'font-display',
  system: 'font-system',
}

/** Theme and font — kept in this browser (cookies), applied at once. */
export function SettingsAppearance() {
  const { theme, setTheme } = useTheme()
  const { font, setFont } = useFont()
  return (
    <>
      <SettingsCard title='Theme'>
        <div role='radiogroup' aria-label='Theme' className='grid gap-3 sm:grid-cols-3'>
          {THEMES.map((t) => (
            <button
              key={t.key}
              type='button'
              role='radio'
              aria-checked={theme === t.key}
              onClick={() => {
                setTheme(t.key)
                toast.success('Saved', { duration: 1500 })
              }}
              className={cn(
                'flex flex-col gap-2.5 rounded-2xl p-3 text-start focus-visible:outline-2 focus-visible:outline-k-6ea8ff',
                theme === t.key ? 'ring-2 ring-kl-202020' : 'ring-1 ring-k-e3e2e2'
              )}
            >
              {/* A picture of the theme: fixed colours on purpose. */}
              <span aria-hidden='true' className={cn('flex h-[72px] flex-col justify-end gap-1.5 rounded-xl p-2.5 ring-1 ring-k-ececec', t.swatch)}>
                <span className={cn('h-2 w-2/3 rounded-full', t.bar)} />
                <span className={cn('h-2 w-1/2 rounded-full', t.bar)} />
              </span>
              <strong className='text-sm text-k-202020'>{t.label}</strong>
            </button>
          ))}
        </div>
      </SettingsCard>
      <SettingsCard title='Font' text='The letters the whole panel uses.'>
        <div role='radiogroup' aria-label='Font' className='flex flex-wrap gap-2'>
          {fonts.map((f) => (
            <button
              key={f}
              type='button'
              role='radio'
              aria-checked={font === f}
              onClick={() => {
                setFont(f)
                toast.success('Saved', { duration: 1500 })
              }}
              className={cn(
                FONT_CLASS[f],
                'h-10 rounded-full px-4 text-sm text-k-202020 focus-visible:outline-2 focus-visible:outline-k-6ea8ff',
                font === f ? 'bg-kb-202020 font-bold text-on-btn' : 'ring-1 ring-k-e3e2e2'
              )}
            >
              {FONT_LABEL[f]}
            </button>
          ))}
        </div>
      </SettingsCard>
      <p className='m-0 text-[13px] text-k-555555'>These stay in this browser only.</p>
    </>
  )
}
