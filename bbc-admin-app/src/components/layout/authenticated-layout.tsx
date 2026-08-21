import { useEffect, useRef } from 'react'
import { Outlet, useNavigate } from '@tanstack/react-router'
import { useHeartbeat } from '@/hooks/use-heartbeat'
import { useReadyStore } from '@/stores/ready-store'
import { getCookie } from '@/lib/cookies'
import { requestNotifyPermission } from '@/lib/notify-assignment'
import { installCrmBridge } from '@/lib/crm-bridge'
import { useQueueStore } from '@/stores/queue-store'
import { isAllowedCrmOrigin } from '@/lib/crm-embed-auth'
import { cn } from '@/lib/utils'
import { LayoutProvider } from '@/context/layout-provider'
import { SearchProvider } from '@/context/search-provider'
import { SidebarInset, SidebarProvider } from '@/components/ui/sidebar'
import { AppSidebar } from '@/components/layout/app-sidebar'
import { SkipToMain } from '@/components/skip-to-main'

type AuthenticatedLayoutProps = {
  children?: React.ReactNode
}

export function AuthenticatedLayout({ children }: AuthenticatedLayoutProps) {
  const viewingConversationId = useReadyStore((s) => s.viewingConversationId)
  useHeartbeat(undefined, viewingConversationId)

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
      }),
    []
  )

  const defaultOpen = getCookie('sidebar_state') !== 'false'
  return (
    <SearchProvider>
      <LayoutProvider>
        <SidebarProvider defaultOpen={defaultOpen}>
          <SkipToMain />
          <AppSidebar />
          <SidebarInset
            className={cn(
              // Set content container, so we can use container queries
              '@container/content',

              // If layout is fixed, set the height
              // to 100svh to prevent overflow
              'has-data-[layout=fixed]:h-svh',

              // If layout is fixed and sidebar is inset,
              // set the height to 100svh - spacing (total margins) to prevent overflow
              'peer-data-[variant=inset]:has-data-[layout=fixed]:h-[calc(100svh-(var(--spacing)*4))]'
            )}
          >
            {children ?? <Outlet />}
          </SidebarInset>
        </SidebarProvider>
      </LayoutProvider>
    </SearchProvider>
  )
}
