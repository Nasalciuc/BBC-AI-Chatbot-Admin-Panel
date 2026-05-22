/**
 * Top 5 most popular flight routes with progress bars.
 * Data source: DashboardStats.top_routes fetched from GET /api/dashboard/stats (FastAPI backend)
 * Parent: src/features/dashboard/index.tsx passes top_routes array as props
 */

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'

interface TopRoutesProps {
  top_routes: Array<{ route: string; count: number }>
}

export function TopRoutes({ top_routes = [] }: TopRoutesProps) {
  if (top_routes.length === 0) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Top Routes</CardTitle>
          <CardDescription>Most popular destinations</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="py-8 text-center text-sm text-muted-foreground">
            No route data yet
          </div>
        </CardContent>
      </Card>
    )
  }

  const maxCount = top_routes[0].count

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Top Routes</CardTitle>
        <CardDescription>Most popular destinations</CardDescription>
      </CardHeader>
      <CardContent>
        {top_routes.map((r) => (
          <div key={r.route} className="mb-4 space-y-1.5 last:mb-0">
            <div className="flex items-center justify-between">
              <span className="text-sm font-semibold">{r.route}</span>
              <span className="text-xs tabular-nums text-muted-foreground">
                {r.count} leads
              </span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-muted/40">
              <div
                className="h-full rounded-full"
                style={{
                  width: `${(r.count / maxCount) * 100}%`,
                  background: 'linear-gradient(90deg, #C9A54E, #0B1829)',
                }}
              />
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  )
}
