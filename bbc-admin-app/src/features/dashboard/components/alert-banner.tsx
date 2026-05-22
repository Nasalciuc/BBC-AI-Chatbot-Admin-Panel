/**
 * Conditional warning banner for unhealthy AI pipeline metrics.
 * Renders null when all metrics are within acceptable thresholds.
 * Data source: DashboardStats fetched from GET /api/dashboard/stats (FastAPI backend)
 * Parent: src/features/dashboard/index.tsx passes latency, fallback rate, and cost metrics
 */

import { useState } from 'react'
import { AlertTriangle, X } from 'lucide-react'

interface AlertBannerProps {
  latency_median_ms: number
  fallback_rate_percent: number
  cost_vs_budget_percent: number
}

export function AlertBanner(props: AlertBannerProps) {
  const {
    latency_median_ms = 0,
    fallback_rate_percent = 0,
    cost_vs_budget_percent = 0,
  } = props

  const [dismissed, setDismissed] = useState(false)

  const alerts: string[] = []
  if (fallback_rate_percent > 10)
    alerts.push(
      `Fallback rate at ${fallback_rate_percent.toFixed(1)}% (threshold: 10%)`
    )
  if (latency_median_ms > 5000)
    alerts.push(
      `Latency at ${(latency_median_ms / 1000).toFixed(1)}s (threshold: 5s)`
    )
  if (cost_vs_budget_percent > 80)
    alerts.push(
      `AI cost at ${cost_vs_budget_percent.toFixed(0)}% of daily budget`
    )

  if (alerts.length === 0 || dismissed) return null

  return (
    <div className="flex items-center gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3">
      <AlertTriangle className="h-5 w-5 shrink-0 text-amber-600" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-amber-800">
          AI Pipeline Warning
        </p>
        <p className="mt-0.5 text-xs text-amber-700">{alerts.join(' · ')}</p>
      </div>
      <a
        href="#"
        className="whitespace-nowrap text-xs font-medium text-amber-700 hover:underline"
      >
        View details →
      </a>
      <button
        onClick={() => setDismissed(true)}
        className="shrink-0 text-amber-400 hover:text-amber-600"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  )
}
