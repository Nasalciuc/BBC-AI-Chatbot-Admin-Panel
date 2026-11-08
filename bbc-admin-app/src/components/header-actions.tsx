/**
 * ONE header, every page. Operators were going blind outside /chats: the
 * Ready toggle and the new-chat notification bell existed only there, so a
 * KB lookup mid-shift silently removed availability control and alerts.
 * ReadyToggle self-hides for roles that don't take chats.
 *
 * The redesign moved the account menu and the theme into the side menu and
 * Settings; what stays here is what an operator needs at a glance.
 */

import { ConnectionBanner } from '@/components/connection-banner'
import { NotificationBell } from '@/components/notification-bell'
import { ReadyToggle } from '@/components/ready-toggle'
import { cn } from '@/lib/utils'

export function HeaderActions({ className, compact = false }: { className?: string; compact?: boolean }) {
  return (
    <div className={cn('ms-auto flex flex-none items-center gap-2.5', className)}>
      <ConnectionBanner />
      <ReadyToggle compact={compact} />
      <NotificationBell />
    </div>
  )
}
