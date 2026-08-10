/**
 * KPI metric cards for the main dashboard.
 * Data source: DashboardStats fetched from GET /api/dashboard/stats (FastAPI backend)
 * Parent: src/features/dashboard/index.tsx passes props from stats object
 */

import {
  PhoneOff,
  MessageSquare,
  Target,
  DollarSign,
  ArrowUpRight,
  ArrowDownRight,
} from 'lucide-react'
import {
  ResponsiveContainer,
  AreaChart,
  Area,
} from 'recharts'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import type { DashboardStats } from '@/lib/types'

type KpiCardsProps = Pick<
  DashboardStats,
  | 'leads_uncalled'
  | 'leads_sla_breach'
  | 'conversations_today'
  | 'conversations_yesterday'
  | 'conversations_active'
  | 'leads_gold'
  | 'leads_silver'
  | 'leads_bronze'
  | 'leads_sparkline_7d'
  | 'cost_today'
  | 'cost_avg_30d'
  | 'daily_budget'
> & {
  showCost?: boolean
}

export function KpiCards(props: KpiCardsProps) {
  const {
    leads_uncalled = 0,
    leads_sla_breach = 0,
    conversations_today = 0,
    conversations_yesterday = 0,
    conversations_active = 0,
    leads_gold = 0,
    leads_silver = 0,
    leads_bronze = 0,
    leads_sparkline_7d = [],
    cost_today = 0,
    cost_avg_30d = 0,
    daily_budget = 50,
    showCost = true,
  } = props

  const costToday = cost_today ?? 0
  const costAvg30d = cost_avg_30d ?? 0
  const dailyBudget = daily_budget ?? 50

  const pctChange =
    conversations_yesterday > 0
      ? (
          ((conversations_today - conversations_yesterday) /
            conversations_yesterday) *
          100
        ).toFixed(1)
      : '0.0'
  const pctNum = parseFloat(pctChange)

  const costPct = dailyBudget > 0 ? (costToday / dailyBudget) * 100 : 0
  const costOverBudget = costToday > dailyBudget
  const costAboveAvg = costToday > costAvg30d * 1.5

  const sparkData = leads_sparkline_7d.map((v) => ({ v }))
  const todayLeads =
    leads_sparkline_7d.length > 0
      ? leads_sparkline_7d[leads_sparkline_7d.length - 1]
      : 0

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      {/* Card 1 — Leads Uncalled */}
      <Card className="hover:shadow-md transition-all duration-200">
        <CardContent className="p-4">
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-red-100">
              <PhoneOff className="h-5 w-5 text-red-600" />
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                Leads Uncalled
              </p>
              <p className="mt-1 text-3xl font-bold">{leads_uncalled}</p>
              <div className="mt-1.5">
                {leads_sla_breach > 0 ? (
                  <Badge variant="destructive" className="animate-pulse">
                    {leads_sla_breach} over SLA!
                  </Badge>
                ) : leads_uncalled === 0 ? (
                  <Badge className="bg-green-100 text-green-700 hover:bg-green-100">
                    All contacted ✓
                  </Badge>
                ) : null}
              </div>
              <a
                href="/leads"
                className="mt-2 inline-block text-sm text-blue-600 hover:underline"
              >
                View leads →
              </a>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Card 2 — Conversations Today */}
      <Card className="hover:shadow-md transition-all duration-200">
        <CardContent className="p-4">
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-blue-100">
              <MessageSquare className="h-5 w-5 text-blue-600" />
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                Conversations Today
              </p>
              <p className="mt-1 text-3xl font-bold">{conversations_today}</p>
              <div className="mt-1.5">
                {pctNum > 0 ? (
                  <span className="inline-flex items-center gap-0.5 text-xs text-green-600">
                    <ArrowUpRight className="h-3 w-3" /> {pctChange}% vs
                    yesterday
                  </span>
                ) : pctNum < 0 ? (
                  <span className="inline-flex items-center gap-0.5 text-xs text-red-600">
                    <ArrowDownRight className="h-3 w-3" /> {Math.abs(pctNum).toFixed(1)}% vs yesterday
                  </span>
                ) : (
                  <span className="text-xs text-muted-foreground">
                    Same as yesterday
                  </span>
                )}
              </div>
              <span className="text-xs text-muted-foreground">
                {conversations_active} active now
              </span>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Card 3 — New Leads Today */}
      <Card className="hover:shadow-md transition-all duration-200">
        <CardContent className="p-4">
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-amber-100">
              <Target className="h-5 w-5 text-amber-700" />
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                New Leads Today
              </p>
              <p className="mt-1 text-3xl font-bold">{todayLeads}</p>
              <div className="mt-1.5">
                {leads_gold > 0 && (
                  <a href="/leads" className="flex items-center gap-1.5 group">
                    <span className="text-lg font-bold text-amber-700">{leads_gold}</span>
                    <span className="text-xs font-medium text-amber-600 group-hover:underline">
                      Gold leads — ready to call
                    </span>
                  </a>
                )}
                <div className="mt-1 flex items-center gap-1.5">
                  <Badge className="border border-slate-300 bg-slate-100 px-1.5 text-[11px] text-slate-700 hover:bg-slate-100">
                    {leads_silver}S
                  </Badge>
                  <Badge className="border border-orange-300 bg-orange-100 px-1.5 text-[11px] text-orange-800 hover:bg-orange-100">
                    {leads_bronze}B
                  </Badge>
                </div>
              </div>
              {sparkData.length > 0 && (
                <div className="mt-2">
                  <ResponsiveContainer width={80} height={28}>
                    <AreaChart data={sparkData}>
                      <Area
                        type="monotone"
                        dataKey="v"
                        stroke="#C9A54E"
                        fill="#C9A54E"
                        fillOpacity={0.2}
                        strokeWidth={1.5}
                        dot={false}
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              )}
            </div>
          </div>
        </CardContent>
      </Card>

      {showCost && (
      <Card
        className={`hover:shadow-md transition-all duration-200 ${costOverBudget ? 'bg-red-50 dark:bg-red-950/40' : ''}`}
      >
        <CardContent className="p-4">
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-emerald-100">
              <DollarSign className="h-5 w-5 text-emerald-600" />
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                AI Cost Today
              </p>
              <p className="mt-1 text-3xl font-bold">
                ${costToday.toFixed(2)}
              </p>
              <div className="mt-1.5">
                {costOverBudget ? (
                  <span className="text-xs font-bold text-red-600">
                    OVER BUDGET!
                  </span>
                ) : costAboveAvg ? (
                  <span className="text-xs text-red-600">
                    vs ${costAvg30d.toFixed(2)} daily avg ⚠ Above average
                  </span>
                ) : (
                  <span className="text-xs text-muted-foreground">
                    vs ${costAvg30d.toFixed(2)} daily avg
                  </span>
                )}
              </div>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted/40">
                <div
                  className={`h-full rounded-full transition-all ${
                    costPct > 80
                      ? 'bg-red-500'
                      : costPct > 60
                        ? 'bg-amber-500'
                        : 'bg-emerald-500'
                  }`}
                  style={{ width: `${Math.min(costPct, 100)}%` }}
                />
              </div>
            </div>
          </div>
        </CardContent>
      </Card>
      )}
    </div>
  )
}
