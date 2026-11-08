import { useEffect, useRef } from 'react'
import { Outlet, useNavigate } from '@tanstack/react-router'
import { useHeartbeat } from '@/hooks/use-heartbeat'
import { useTaskReminders } from '@/features/tasks/use-task-alerts'
import { useReadyStore } from '@/stores/ready-store'
import { getCookie } from '@/lib/cookies'
import { requestNotifyPermission } from '@/lib/notify-assignment'
import { installCrmBridge, isEmbedded } from '@/lib/crm-bridge'
import { installPanelLeader } from '@/lib/panel-leader'
import { usePanelModeStore } from '@/stores/panel-mode-store'
import { useQueueStore } from '@/stores/queue-store'
import { isAllowedCrmOrigin } from '@/lib/crm-embed-auth'
import { useAuthStore } from '@/stores/auth-store'
import { cn } from '@/lib/utils'
import { LayoutProvider } from '@/context/layout-provider'
import { SearchProvider } from '@/context/search-provider'
import { SidebarInset, SidebarProvider } from '@/components/ui/sidebar'
import { AppSidebar } from '@/components/layout/app-sidebar'
import { MobileTabBar } from '@/components/layout/mobile-tab-bar'
import { isOperatorRole } from '@/components/layout/data/sidebar-data'
import { SkipToMain } from '@/components/skip-to-main'
import { StatusBands } from '@/components/layout/status-bands'

type AuthenticatedLayoutProps = {
  children?: React.ReactNode
}

export function AuthenticatedLayout({ children }: AuthenticatedLayoutProps) {
  const viewingConversationId = useReadyStore((s) => s.viewingConversationId)
  useHeartbeat(undefined, viewingConversationId)
  // Task reminders: 15 min before, at the due time, and 30 min late for supervisors.
  useTaskReminders()

  const navigate = useNavigate()
  const navigateRef = useRef(navigate)
  navigateRef.current = navigate

  useEffect(() => {
    requestNotifyPermission()
  }, [])

  // Tell the CRM when a conversation is waiting. Mounted here, next to the
  // heartbeat that feeds it. The bridge is a no-op unless we are inside an
  // iframe, so a panel opened in its own tab behaves exactly as before.
  useEffect(
    () =>
      installCrmBridge({
        isAllowedOrigin: isAllowedCrmOrigin,
        // Read at handshake time, not captured at mount: the store's value
        // moves with every heartbeat and a closure would freeze it at zero.
        getQueueCount: () => useQueueStore.getState().queueCount,
        // Through a ref: putting `navigate` in the effect's dependency list
        // would remount the bridge on every navigation, losing handshakeOrigin
        // and silently killing the agent's alerts until the next crm:hello.
        onFocusQueue: () => navigateRef.current({ to: '/chats' }),
        onVisibility: (hidden) =>
          usePanelModeStore.getState().setReason('crm_hidden', hidden),
      }),
    []
  )

  // A background tab is the only case the browser tells us about by itself.
  useEffect(() => {
    const set = usePanelModeStore.getState().setReason
    const onVis = () => set('tab_hidden', document.hidden)
    onVis()
    document.addEventListener('visibilitychange', onVis)
    return () => document.removeEventListener('visibilitychange', onVis)
  }, [])

  // One consumer per human: the embedded panel wins, a standalone tab dozes.
  useEffect(() => installPanelLeader(), [])

  const defaultOpen = getCookie('sidebar_state') !== 'false'
  // Inside the CRM dock the panel is the chat list and nothing else: no side
  // menu. An agent on a phone gets the tab bar (Chats, Knowledge, Me).
  const embedded = isEmbedded()
  const role = useAuthStore((s) => s.auth.user?.role)
  const tabBar = !embedded && isOperatorRole(role)
  return (
    <SearchProvider>
      <LayoutProvider>
        <SidebarProvider
          defaultOpen={defaultOpen}
          style={{ '--sidebar-width': '16rem', '--sidebar-width-icon': '4.5rem' } as React.CSSProperties}
        >
          <SkipToMain />
          {!embedded && <AppSidebar />}
          <SidebarInset
            className={cn(
              // Set content container, so we can use container queries
              '@container/content',
              'bg-background',
              tabBar && 'max-md:pb-16',

              // If layout is fixed, set the height
              // to 100svh to prevent overflow
              'has-data-[layout=fixed]:h-svh',

              // If layout is fixed and sidebar is inset,
              // set the height to 100svh - spacing (total margins) to prevent overflow
              'peer-data-[variant=inset]:has-data-[layout=fixed]:h-[calc(100svh-(var(--spacing)*4))]'
            )}
          >
            {/* Offline, server not answering, session ending, or this tab
                paused because it's active in the CRM: one band at a time. */}
            <StatusBands />
            {children ?? <Outlet />}
          </SidebarInset>
          {tabBar && <MobileTabBar />}
        </SidebarProvider>
      </LayoutProvider>
    </SearchProvider>
  )
}
