import { useEffect, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { checkHealth } from '@/lib/api'
import { ErrorFrame } from './frame'

/** The panel is being updated. It checks the server every 15 s by itself
 *  and goes back to the panel as soon as it answers. */
export function MaintenanceError() {
  const navigate = useNavigate()
  const [back, setBack] = useState(false)
  useEffect(() => {
    const check = async () => {
      // Still updating: the next check is in 15 s.
      if (await checkHealth()) {
        setBack(true)
        navigate({ to: '/', replace: true })
      }
    }
    const t = setInterval(check, 15_000)
    return () => clearInterval(t)
  }, [navigate])
  return (
    <ErrorFrame
      dark
      kicker='503'
      title='The panel is being updated'
      text={back ? 'It’s back. Opening the panel…' : 'Try again in a few minutes. This page checks by itself and opens the panel when it’s ready.'}
    />
  )
}
