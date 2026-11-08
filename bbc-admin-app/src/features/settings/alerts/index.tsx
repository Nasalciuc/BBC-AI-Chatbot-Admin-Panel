import { useState } from 'react'
import { toast } from 'sonner'
import {
  DEFAULT_ALERT_PREFS,
  PERMISSION_TEXT,
  SOUND_LABEL,
  notificationText,
  readAlertPrefs,
  volumeLow,
  writeAlertPrefs,
  type AlertPrefs,
  type NotifyPermission,
  type RingSound,
} from '@/lib/alert-prefs'
import {
  askNotifyPermission,
  canReceiveAssignNotifications,
  notifyPermission,
  previewRing,
  testAlert,
} from '@/lib/notify-assignment'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth-store'
import { useReadyStore } from '@/stores/ready-store'
import { Switch } from '@/components/ui/switch'
import { SettingsCard } from '../components/card'
import { pillDark, pillLine } from '../components/styles'

const SOUNDS: RingSound[] = ['classic', 'soft', 'two']

function Toggle({ id, label, help, on, onChange }: { id: string; label: string; help: string; on: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className='flex items-start gap-3.5'>
      <Switch id={id} checked={on} onCheckedChange={onChange} className='mt-0.5' aria-describedby={`${id}-help`} />
      <label htmlFor={id} className='flex flex-col gap-0.5'>
        <strong className='text-[15px] text-k-202020'>{label}</strong>
        <span id={`${id}-help`} className='text-[13px] leading-[1.45] text-k-555555'>
          {help}
        </span>
      </label>
    </div>
  )
}

/**
 * How a new chat reaches you on this device: the ring, the flashing tab,
 * the browser notification, and the queue's short sound. Kept in this
 * browser (lib/alert-prefs.ts); the alert engine reads it at alert time.
 */
export function SettingsAlerts() {
  const role = useAuthStore((s) => s.auth.user?.role)
  const seconds = useReadyStore((s) => s.responseDeadlineSeconds)
  const [prefs, setPrefs] = useState<AlertPrefs>(readAlertPrefs)
  const [perm, setPerm] = useState<NotifyPermission>(notifyPermission)
  const [testNote, setTestNote] = useState('')

  if (!canReceiveAssignNotifications(role)) {
    return (
      <SettingsCard
        title='Nothing rings for QA'
        text='QA reads chats and reviews leads but never gets chats, so the panel has no new-chat alerts for you.'
      />
    )
  }

  const set = (patch: Partial<AlertPrefs>, note = 'Saved') => {
    const next = { ...prefs, ...patch }
    setPrefs(next)
    if (writeAlertPrefs(next)) toast.success(note, { duration: 1500 })
    else toast.error('This browser won’t store settings, so this holds until you reload.')
  }

  const allow = async () => {
    const p = await askNotifyPermission()
    setPerm(p)
    if (p === 'granted') toast.success('Notifications allowed')
    else if (p === 'denied') toast.error('The browser blocked notifications.')
  }

  const test = () => {
    const r = testAlert()
    if (!r) {
      setTestNote('A real chat is ringing right now, so the test waits.')
      return
    }
    const parts = [r.rang && 'ring', r.flashed && 'tab', r.notified && 'notification'].filter(Boolean) as string[]
    const what = parts.length ? `Test sent: ${parts.join(', ').replace(/, ([^,]*)$/, ' and $1')}.` : 'Test sent, but the ring and the tab are both off.'
    setTestNote(r.notified || perm === 'unsupported' ? what : `${what} Notifications aren’t allowed yet.`)
  }

  const sample = notificationText('new', 'Michael T.', seconds)
  const permText = PERMISSION_TEXT[perm]
  const badge = { granted: 'bg-k-e3f6ea text-k-0a6b35', default: 'bg-k-fff1dc text-k-8a4b00', denied: 'bg-k-fdecea text-k-b3261e', unsupported: 'bg-k-f2f2f2 text-k-444444' }[perm]

  return (
    <>
      <SettingsCard
        title='When a chat is given to you'
        text={`You have ${seconds} seconds to answer before the AI answers for you. These make sure you notice.`}
      >
        <Toggle id='al-ring' label='Ring until I open it' help='A ring that repeats, like a phone, and stops when you open the chat.' on={prefs.ring} onChange={(v) => set({ ring: v }, v ? 'Ring on' : 'Ring off')} />
        <Toggle id='al-flash' label='Flash the tab' help='The tab’s name blinks “New chat”, so you see it from another tab.' on={prefs.flash} onChange={(v) => set({ flash: v }, v ? 'Tab flashing on' : 'Tab flashing off')} />
        {!prefs.ring && (
          <p role='status' className='m-0 rounded-xl bg-k-fff3e0 px-3.5 py-2.5 text-[13px] leading-[1.45] text-k-8a4b00'>
            With the ring off, you hear nothing when a chat is given to you.{' '}
            {prefs.flash ? 'The tab still flashes and the toast in the corner still shows it.' : 'The toast in the corner still shows it.'}
          </p>
        )}
        {prefs.ring && (
          <div className='flex flex-col gap-3 rounded-2xl bg-k-f7f7f7 p-4'>
            <div className='flex flex-wrap items-center gap-3'>
              <span className='w-16 text-sm font-bold text-k-202020'>Sound</span>
              <div role='radiogroup' aria-label='Ring sound' className='flex rounded-xl bg-k-ececec p-1'>
                {SOUNDS.map((s) => (
                  <button
                    key={s}
                    type='button'
                    role='radio'
                    aria-checked={prefs.sound === s}
                    onClick={() => set({ sound: s })}
                    className={cn(
                      'h-8 rounded-lg px-3 text-[13px] text-k-202020 focus-visible:outline-2 focus-visible:outline-k-6ea8ff',
                      prefs.sound === s ? 'bg-k-ffffff font-bold shadow-sm' : ''
                    )}
                  >
                    {SOUND_LABEL[s]}
                  </button>
                ))}
              </div>
              <button type='button' onClick={() => previewRing(prefs.sound, prefs.volume)} className={pillLine}>
                <span aria-hidden='true'>▶</span> Play
              </button>
            </div>
            <label className='flex flex-wrap items-center gap-3'>
              <span className='w-16 text-sm font-bold text-k-202020'>Volume</span>
              <input
                type='range'
                min={0}
                max={100}
                step={5}
                value={prefs.volume}
                onChange={(e) => {
                  // Stored on every change (a screen reader's swipe has no pointer-up);
                  // the "Saved" note waits for the release.
                  const next = { ...prefs, volume: Number(e.target.value) }
                  setPrefs(next)
                  writeAlertPrefs(next)
                }}
                onPointerUp={() => set({ volume: prefs.volume })}
                onKeyUp={(e) => {
                  if (/^(Arrow|Home|End|Page)/.test(e.key)) set({ volume: prefs.volume })
                }}
                className='w-full max-w-[280px] accent-k-202020'
              />
              <span className='w-10 text-sm text-k-202020 tabular-nums'>{prefs.volume}%</span>
            </label>
            {volumeLow(prefs) && (
              <span role='status' className='text-[13px] text-k-8a4b00'>
                At this volume you may miss a chat in a noisy room.
              </span>
            )}
          </div>
        )}

        <div className='flex items-start gap-3.5 border-t border-k-ececec pt-4'>
          <span className={cn('mt-0.5 flex h-6 min-w-11 flex-none items-center justify-center rounded-full px-2 text-[11px] font-bold', badge)}>{permText.badge}</span>
          <div className='flex flex-col gap-1.5'>
            <strong className='text-[15px] text-k-202020'>Browser notification</strong>
            <span className='text-[13px] leading-[1.45] text-k-555555'>{permText.text}</span>
            {perm === 'default' && (
              <button type='button' onClick={allow} className={cn(pillDark, 'h-9 self-start text-[13px]')}>
                Allow notifications
              </button>
            )}
            {perm === 'denied' && (
              <span className='text-[13px] leading-[1.45] text-k-202020'>
                To turn them on: click the lock icon next to the address, open <strong>Notifications</strong>, choose <strong>Allow</strong>, then reload the panel.
              </span>
            )}
            {perm !== 'unsupported' && (
              <div className='mt-1 flex flex-col gap-1.5'>
                <span className='text-[13px] font-bold text-k-555555'>The notification looks like this</span>
                <div aria-label='Example notification' className='flex max-w-[420px] gap-3 rounded-2xl bg-k-ffffff p-3.5 ring-1 ring-k-ececec'>
                  <span aria-hidden='true' className='flex size-9 flex-none items-center justify-center rounded-lg bg-kb-202020 text-sm font-bold text-on-btn'>
                    B
                  </span>
                  <span className='flex flex-col gap-0.5'>
                    <strong className='text-sm text-k-202020'>{sample.title}</strong>
                    <span className='text-[13px] text-k-202020'>{sample.body}</span>
                    <span className='text-[11px] text-k-6b6b6b'>BBC Admin Panel · now</span>
                  </span>
                </div>
                <span className='text-[13px] leading-[1.45] text-k-555555'>
                  Only the first name and initial, so a notification on a shared screen doesn’t show the full name.
                </span>
              </div>
            )}
          </div>
        </div>

        <div className='flex flex-wrap items-center gap-3 rounded-2xl bg-k-f7f7f7 px-4 py-3'>
          <span className='min-w-[200px] flex-1 text-[13px] leading-[1.45] text-k-202020'>
            {testNote || 'Check everything together: the ring, the tab and the notification, the way a real chat would.'}
          </span>
          <button type='button' onClick={test} className={pillDark}>
            Send me a test alert
          </button>
        </div>
      </SettingsCard>

      <SettingsCard title='Chats waiting in the queue'>
        <Toggle
          id='al-beep'
          label='A short sound for each new waiting chat'
          help='One quiet sound, no flashing: someone could take it, nobody gave it to you yet.'
          on={prefs.beep}
          onChange={(v) => set({ beep: v }, v ? 'Queue sound on' : 'Queue sound off')}
        />
      </SettingsCard>

      <SettingsCard
        title='On your phone'
        tone='grey'
        text={`New chats reach you only while the panel is open and the screen is on. With the screen locked the phone doesn’t ring, and after 90 seconds out of sight you leave the queue.`}
      />

      <p className='m-0 flex flex-wrap items-center gap-x-3 text-[13px] text-k-555555'>
        Saved in this browser. Set them on each computer and phone you use.
        {JSON.stringify(prefs) !== JSON.stringify(DEFAULT_ALERT_PREFS) && (
          <button type='button' onClick={() => set({ ...DEFAULT_ALERT_PREFS }, 'Back to the usual alerts')} className='font-bold text-k-5b45e0 hover:underline'>
            Back to the usual alerts
          </button>
        )}
      </p>
    </>
  )
}
