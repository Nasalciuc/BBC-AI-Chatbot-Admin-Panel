/**
 * Hot leads list — uncontacted high-value leads requiring immediate action.
 * Data source: DashboardStats.hot_leads fetched from GET /api/dashboard/stats (FastAPI backend)
 * Parent: src/features/dashboard/index.tsx passes hot_leads array as props
 */

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import type { DashboardStats } from '@/lib/types'
import { formatDuration } from '@/lib/format-age'
import { isDegenerateRoute } from '@/lib/route-display'

interface HotLeadsProps {
  hot_leads: DashboardStats['hot_leads']
}

const tierStyles: Record<
  'gold' | 'silver' | 'bronze',
  string
> = {
  gold: 'border border-amber-300 bg-amber-100 text-amber-800 hover:bg-amber-100 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300 dark:hover:bg-amber-950',
  silver:
    'border border-slate-300 bg-slate-100 text-slate-700 hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-slate-900',
  bronze:
    'border border-orange-300 bg-orange-100 text-orange-800 hover:bg-orange-100 dark:border-orange-800 dark:bg-orange-950 dark:text-orange-300 dark:hover:bg-orange-950',
}

export function HotLeads({ hot_leads = [] }: HotLeadsProps) {
  const leads = hot_leads.filter((l) => !isDegenerateRoute(l.route))
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Hot Leads 🔥</CardTitle>
        <CardDescription>Uncontacted high-value leads</CardDescription>
      </CardHeader>
      <CardContent>
        {leads.length === 0 ? (
          <div className="rounded-lg bg-green-50 dark:bg-green-950/40 p-6 text-center">
            <span className="text-2xl">🎉</span>
            <p className="mt-2 text-sm font-medium text-green-700 dark:text-green-300">
              All leads contacted!
            </p>
          </div>
        ) : (
          <>
            {leads.map((lead) => (
              <div
                key={lead.id}
                className="flex items-center justify-between border-b py-3 last:border-b-0"
              >
                {/* Left — route & name */}
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold">
                    {lead.route}
                  </p>
                  <p className="truncate text-xs text-muted-foreground">
                    {lead.visitor_name ?? 'Unknown visitor'}
                  </p>
                </div>

                {/* Center — score badge */}
                <div className="mx-3 shrink-0">
                  <Badge className={`font-mono ${tierStyles[lead.tier]}`}>
                    {lead.score}
                  </Badge>
                </div>

                {/* Right — time */}
                <div className="shrink-0 text-right">
                  {lead.minutes_since_created < 120 ? (
                    <span className="text-xs font-medium text-green-600">
                      {formatDuration(lead.minutes_since_created)}
                    </span>
                  ) : lead.minutes_since_created < 180 ? (
                    <span className="text-xs font-medium text-amber-600">
                      {formatDuration(lead.minutes_since_created)} ⚠
                    </span>
                  ) : (
                    <span className="animate-pulse text-xs font-bold text-red-600">
                      {formatDuration(lead.minutes_since_created)} 🔴
                    </span>
                  )}
                </div>
              </div>
            ))}
            <div className="mt-1 pt-3">
              <a
                href="/leads"
                className="text-xs font-medium text-blue-600 hover:underline"
              >
                View all leads →
              </a>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}
