/**
 * ONE header, every page. Operators were going blind outside /chats: the
 * Ready toggle and the new-chat notification bell existed only there, so a
 * KB lookup mid-shift silently removed availability control and alerts.
 * ReadyToggle self-hides for management/read-only roles.
 */

import { ConnectionBanner } from '@/components/connection-banner'
import { NotificationBell } from '@/components/notification-bell'
import { ProfileDropdown } from '@/components/profile-dropdown'
import { ReadyToggle } from '@/components/ready-toggle'
import { ThemeSwitch } from '@/components/theme-switch'

export function HeaderActions() {
  return (
    <div className='ms-auto flex items-center space-x-4'>
      <ConnectionBanner />
      <ReadyToggle />
      <NotificationBell />
      <ThemeSwitch />
      <ProfileDropdown />
    </div>
  )
}
