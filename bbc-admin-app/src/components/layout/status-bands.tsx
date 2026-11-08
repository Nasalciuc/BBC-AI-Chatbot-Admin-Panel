import { useEffect, useRef, useState } from 'react'
import { useRouter } from '@tanstack/react-router'
import { WifiOff } from 'lucide-react'
import { backTo, bandText, pickBand, secondsToNext } from '@/lib/bands'
import { isEmbedded } from '@/lib/crm-bridge'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth-store'
import { usePanelModeStore } from '@/stores/panel-mode-store'
import { useReadyStore } from '@/stores/ready-store'

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'
const CHECK_MS = 30_000

/**
 * The one band at the top of the panel (lib/bands.ts): offline, server not
 * answering, session ending, or this tab paused. One /health check for the
 * whole panel, every 30 s, and none while nobody is looking.
 */
export function StatusBands() {
  const router = useRouter()
  const dormant = usePanelModeStore((s) => s.dormant)
  const notLeader = usePanelModeStore((s) => s.reasons.has('not_leader'))
  const exp = useAuthStore((s) => s.auth.user?.exp ?? null)
  const [online, setOnline] = useState(() => typeof navigator === 'undefined' || navigator.onLine !== false)
  // null until the first check answers: no "can't reach" flash on load.
  const [reachable, setReachable] = useState<boolean | null>(null)
  const [nowMs, setNowMs] = useState(() => Date.now())
  const [checkedAt, setCheckedAt] = useState(0)
  const checkNow = useRef<() => void>(() => {})

  useEffect(() => {
    const on = () => setOnline(true)
    const off = () => setOnline(false)
    window.addEventListener('online', on)
    window.addEventListener('offline', off)
    return () => {
      window.removeEventListener('online', on)
      window.removeEventListener('offline', off)
    }
  }, [])

  useEffect(() => {
    if (dormant) return // nothing to check for a panel nobody is looking at
    const check = async () => {
      try {
        const res = await fetch(`${API}/health`, { signal: AbortSignal.timeout(8000) })
        setReachable(res.ok)
      } catch {
        setReachable(false)
      }
      setCheckedAt(Date.now())
    }
    checkNow.current = () => void check()
    check()
    const interval = setInterval(check, 30000)
    return () => clearInterval(interval)
  }, [dormant])

  const embedded = isEmbedded()
  const kind = pickBand({ online, serverReachable: reachable, expMs: exp, nowMs, embedded, notLeader })

  // A clock: every second while counting down to the next check, else
  // every 30 s so the session band appears on time.
  useEffect(() => {
    const t = setInterval(() => setNowMs(Date.now()), kind === 'server' || kind === 'session' ? 1000 : 30_000)
    return () => clearInterval(t)
  }, [kind])

  if (!kind) return null
  const t = bandText(kind, { retryInS: secondsToNext(checkedAt, CHECK_MS, nowMs), expMs: exp, nowMs })
  const signInAgain = () => {
    const loc = router.history.location
    const redirect = backTo(loc.pathname, loc.href, useReadyStore.getState().viewingConversationId)
    router.navigate({ to: '/sign-in', search: { redirect } })
  }
  const tone = {
    offline: 'bg-k-fff3e0 text-k-8a4b00',
    server: 'bg-k-fff3e0 text-k-8a4b00',
    session: 'bg-k-f7f7f7 text-k-202020',
    paused: 'bg-nav text-nav-active-fg',
  }[kind]
  return (
    <div role='status' className={cn('flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-2 text-[13px] leading-[1.45] md:px-7', tone)}>
      {kind === 'offline' && <WifiOff aria-hidden='true' className='size-4 flex-none' />}
      <span className='min-w-0 flex-1'>
        <strong>{t.head}</strong>{' '}
        {/* The countdown changes every second: shown, not read out each time. */}
        <span aria-hidden={kind === 'server' || undefined}>{t.text}</span>
        {kind === 'server' && <span className='sr-only'>Trying again shortly. What you type is kept.</span>}
      </span>
      {kind === 'server' && (
        <button type='button' onClick={() => checkNow.current()} className='h-8 rounded-full bg-kb-202020 px-3.5 text-[13px] font-bold text-on-btn'>
          Try now
        </button>
      )}
      {kind === 'session' && (
        <button type='button' onClick={signInAgain} className='h-8 rounded-full bg-kb-202020 px-3.5 text-[13px] font-bold text-on-btn'>
          Sign in again
        </button>
      )}
    </div>
  )
}
