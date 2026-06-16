import { useEffect } from 'react'
import { Outlet } from '@tanstack/react-router'
import { useHeartbeat } from '@/hooks/use-heartbeat'
import { getCookie } from '@/lib/cookies'
import { requestNotifyPermission } from '@/lib/notify-assignment'
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
  useHeartbeat()

  useEffect(() => {
    requestNotifyPermission()
  }, [])

  const defaultOpen = getCookie('sidebar_state') !== 'false'
  return (
    <SearchProvider>
      <LayoutProvider>
        <SidebarProvider defaultOpen={defaultOpen}>
          {'Notification' in window && Notification.permission === 'denied' && (
            <div className="bg-yellow-900/30 border-b border-yellow-700/50 px-4 py-1.5 text-xs text-yellow-400 text-center">
              ⚠️ Browser notifications blocked — click 🔒 in URL bar → Notifications → Allow to receive chat alerts.
            </div>
          )}
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
