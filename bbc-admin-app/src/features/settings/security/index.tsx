import { useEffect, useState } from 'react'
import { useAuthStore } from '@/stores/auth-store'
import { SettingsCard } from '../components/card'
import { sessionText } from '../view'

/** What the panel can and can't do about your password and sessions today. */
export function SettingsSecurity() {
  const exp = useAuthStore((s) => s.auth.user?.exp)
  const [nowMs, setNowMs] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNowMs(Date.now()), 60_000)
    return () => clearInterval(t)
  }, [])
  return (
    <>
      <SettingsCard
        title='Password'
        text='You can’t change your password here yet, and “Forgot password?” on the sign-in page doesn’t send anything. Ask the owner or an admin to reset it for you.'
      />
      <SettingsCard title='This session' text={sessionText(exp, nowMs)} />
    </>
  )
}
