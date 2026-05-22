/**
 * AI pipeline health traffic-light indicators.
 * Data source: DashboardStats fetched from GET /api/dashboard/stats (FastAPI backend)
 * Parent: src/features/dashboard/index.tsx passes latency, fallback rate, and cost metrics
 */

interface AiHealthProps {
  latency_median_ms: number
  fallback_rate_percent: number
  cost_vs_budget_percent: number
}

const trafficLight = (value: number, thresholds: [number, number]): string =>
  value < thresholds[0]
    ? '#22c55e'
    : value < thresholds[1]
      ? '#f59e0b'
      : '#ef4444'

export function AiHealthIndicator(props: AiHealthProps) {
  const {
    latency_median_ms = 0,
    fallback_rate_percent = 0,
    cost_vs_budget_percent = 0,
  } = props

  const indicators = [
    {
      label: `Lat: ${(latency_median_ms / 1000).toFixed(1)}s`,
      color: trafficLight(latency_median_ms, [3000, 5000]),
      tooltip:
        'AI response latency (median). Green < 3s, Yellow 3-5s, Red > 5s',
    },
    {
      label: `FB: ${fallback_rate_percent.toFixed(1)}%`,
      color: trafficLight(fallback_rate_percent, [5, 10]),
      tooltip:
        'AI fallback rate (template used instead of LLM). Green < 5%, Yellow 5-10%, Red > 10%',
    },
    {
      label: `Cost: ${cost_vs_budget_percent.toFixed(0)}%`,
      color: trafficLight(cost_vs_budget_percent, [60, 80]),
      tooltip: 'Daily AI cost vs $50 budget',
    },
  ]

  return (
    <div className="flex items-center gap-3 rounded-lg bg-muted/30 px-3 py-2">
      {indicators.map((ind) => (
        <div
          key={ind.label}
          className="flex items-center gap-1.5"
          title={ind.tooltip}
        >
          <div
            className="h-2 w-2 rounded-full"
            style={{ backgroundColor: ind.color }}
          />
          <span className="text-[11px] text-muted-foreground">{ind.label}</span>
        </div>
      ))}
    </div>
  )
}
