import { useEffect, useState } from 'react'
import { Wifi, WifiOff } from 'lucide-react'
import { usePanelModeStore } from '@/stores/panel-mode-store'

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

interface HealthResponse {
  status: string
  version: string
  services: Record<string, string>
}

export function ConnectionBanner() {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  // null until the first check answers: no "Offline" flash on every page load.
  const [isLive, setIsLive] = useState<boolean | null>(null)
  const dormant = usePanelModeStore((s) => s.dormant)

  useEffect(() => {
    if (dormant) return // nothing to check for a panel nobody is looking at
    const check = async () => {
      try {
        const res = await fetch(`${API}/health`, { signal: AbortSignal.timeout(3000) })
        if (!res.ok) throw new Error()
        const data: HealthResponse = await res.json()
        setHealth(data)
        setIsLive(['healthy', 'degraded', 'ok'].includes(data.status ?? ''))
      } catch {
        setIsLive(false)
        setHealth(null)
      }
    }
    check()
    const interval = setInterval(check, 30000)
    return () => clearInterval(interval)
  }, [dormant])

  // Quiet when all is well (the design shows nothing); a pill only when the
  // server is out of reach, or answers degraded.
  if (isLive === null || (isLive && health?.status !== 'degraded')) return null
  return (
    <span
      role='status'
      className='flex h-10 flex-none items-center gap-2 rounded-full bg-k-fff3e0 px-3.5 text-[13px] font-bold text-k-8a4b00'
    >
      {isLive ? <Wifi aria-hidden='true' className='size-3.5' /> : <WifiOff aria-hidden='true' className='size-3.5' />}
      {isLive ? 'Server degraded' : 'Offline · trying again'}
    </span>
  )
}
