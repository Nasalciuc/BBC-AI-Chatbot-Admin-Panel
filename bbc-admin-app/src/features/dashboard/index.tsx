import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { ConnectionBanner } from '@/components/connection-banner'
import { ProfileDropdown } from '@/components/profile-dropdown'
import { ThemeSwitch } from '@/components/theme-switch'
import { getDashboardStats } from '@/lib/api'
import type { DashboardStats } from '@/lib/types'
import type { UserRole } from '@/lib/bbc/types'
import { usePermissions } from '@/lib/bbc/hooks'
import { useAuthStore } from '@/stores/auth-store'
import { useQuery } from '@tanstack/react-query'
import { AlertBanner } from './components/alert-banner'
import { KpiCards } from './components/kpi-cards'
import { ConversationsTrend } from './components/conversations-trend'
import { HotLeads } from './components/hot-leads'
import { TopRoutes } from './components/top-routes'
import { LeadFunnel } from './components/lead-funnel'
import { AiHealthIndicator } from './components/ai-health-indicator'

export function Dashboard() {
  const role = useAuthStore((s) => s.auth.user?.role ?? 'sales') as UserRole
  const { canViewDashboardGlobal: canViewGlobal } = usePermissions(role)

  const { data: stats } = useQuery<DashboardStats>({
    queryKey: ['dashboard-stats'],
    queryFn: () => getDashboardStats(),
    staleTime: 0,
    refetchInterval: 60_000,
  })

  return (
    <>
      <Header>
        <div className='ms-auto flex items-center space-x-4'>
          <ConnectionBanner />
          <ThemeSwitch />
          <ProfileDropdown />
        </div>
      </Header>
      <Main>
        <div className='space-y-6'>
          {/* TITLE ROW */}
          <div>
            <h1 className='text-2xl font-bold tracking-tight'>Dashboard</h1>
            <p className='mt-0.5 text-xs text-muted-foreground'>
              BBC AI Chatbot — {canViewGlobal ? 'Admin Overview' : 'My Dashboard'}
            </p>
          </div>

          {stats ? (
            <div className='space-y-6'>
              {canViewGlobal && (
                <AlertBanner
                  latency_median_ms={stats.latency_median_ms ?? 0}
                  fallback_rate_percent={stats.fallback_rate_percent ?? 0}
                  cost_vs_budget_percent={stats.cost_vs_budget_percent ?? 0}
                />
              )}
              <KpiCards
                leads_uncalled={stats.leads_uncalled}
                leads_sla_breach={stats.leads_sla_breach}
                conversations_today={stats.conversations_today}
                conversations_yesterday={stats.conversations_yesterday}
                conversations_active={stats.conversations_active}
                leads_gold={stats.leads_gold}
                leads_silver={stats.leads_silver}
                leads_bronze={stats.leads_bronze}
                leads_sparkline_7d={stats.leads_sparkline_7d}
                cost_today={stats.cost_today ?? 0}
                cost_avg_30d={stats.cost_avg_30d ?? 0}
                daily_budget={stats.daily_budget ?? 50}
                showCost={canViewGlobal}
              />
              <div className='grid gap-6 md:grid-cols-2'>
                <ConversationsTrend conversations_trend_v2={stats.conversations_trend_v2 ?? []} />
                <LeadFunnel funnel={stats.funnel} />
              </div>
              <div className='grid gap-6 md:grid-cols-2'>
                <HotLeads hot_leads={stats.hot_leads} />
                <div className='space-y-6'>
                  {canViewGlobal && <TopRoutes top_routes={stats.top_routes} />}
                  {canViewGlobal && (
                    <AiHealthIndicator
                      latency_median_ms={stats.latency_median_ms ?? 0}
                      fallback_rate_percent={stats.fallback_rate_percent ?? 0}
                      cost_vs_budget_percent={stats.cost_vs_budget_percent ?? 0}
                    />
                  )}
                </div>
              </div>
            </div>
          ) : (
            <div className='flex items-center justify-center py-16 text-sm text-muted-foreground'>
              Loading dashboard stats...
            </div>
          )}
        </div>
      </Main>
    </>
  )
}
