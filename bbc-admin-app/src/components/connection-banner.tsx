import { useEffect, useState } from 'react'
import { Wifi, WifiOff } from 'lucide-react'

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

interface HealthResponse {
  status: string
  version: string
  services: Record<string, string>
}

export function ConnectionBanner() {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [isLive, setIsLive] = useState(false)

  useEffect(() => {
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
  }, [])

  return (
    <div className={`flex items-center gap-2 px-3 py-1.5 text-xs font-medium rounded-full ${
      isLive
        ? 'bg-green-50 text-green-700 border border-green-200 dark:bg-green-950 dark:text-green-400 dark:border-green-800'
        : 'bg-amber-50 text-amber-700 border border-amber-200 dark:bg-amber-950 dark:text-amber-400 dark:border-amber-800'
    }`}>
      {isLive ? (
        <>
          <Wifi className='w-3 h-3' />
          <span>Connected</span>
          {health?.status === 'degraded' && (
            <span className='text-amber-600 ml-1'>(degraded)</span>
          )}
        </>
      ) : (
        <>
          <WifiOff className='w-3 h-3' />
          <span>Offline</span>
        </>
      )}
    </div>
  )
}
