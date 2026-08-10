/**
 * Horizontal lead funnel visualization showing pipeline stages.
 * Data source: DashboardStats.funnel fetched from GET /api/dashboard/stats (FastAPI backend)
 * Parent: src/features/dashboard/index.tsx passes funnel array as props
 */

import { useState, useEffect } from 'react'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'

interface LeadFunnelProps {
  funnel: Array<{ name: string; count: number; color: string }>
}

export function LeadFunnel({ funnel = [] }: LeadFunnelProps) {
  const [animated, setAnimated] = useState(false)

  useEffect(() => {
    const timer = setTimeout(() => setAnimated(true), 100)
    return () => clearTimeout(timer)
  }, [])

  if (funnel.length === 0) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Lead Pipeline</CardTitle>
          <CardDescription>Conversion funnel across all leads</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="py-8 text-center text-sm text-muted-foreground">
            No funnel data yet
          </div>
        </CardContent>
      </Card>
    )
  }

  const maxCount = Math.max(...funnel.map((s) => s.count), 1)
  const firstStage = funnel[0]
  const convertedStage = funnel.find((s) => s.name === 'Converted')
  const lostStage = funnel.find((s) => s.name === 'Lost')

  const conversionPct =
    firstStage && convertedStage && firstStage.count > 0
      ? Math.round((convertedStage.count / firstStage.count) * 100)
      : 0
  const lostPct =
    firstStage && lostStage && firstStage.count > 0
      ? Math.round((lostStage.count / firstStage.count) * 100)
      : 0

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Lead Pipeline</CardTitle>
        <CardDescription>Conversion funnel across all leads</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="space-y-3">
          {funnel.map((stage, i) => {
            const widthPct = (stage.count / maxCount) * 100
            const prevStage = i > 0 ? funnel[i - 1] : null
            const dropOffPct =
              prevStage && prevStage.count > 0
                ? Math.round((stage.count / prevStage.count) * 100)
                : null

            return (
              <div key={stage.name} className="flex items-center gap-3">
                <span className="w-24 shrink-0 text-sm font-medium">
                  {stage.name}
                </span>
                <div className="flex flex-1 items-center gap-2">
                  <div className="h-8 flex-1 overflow-hidden rounded-full bg-muted/30">
                    {/* scaleX, not width: width transitions relayout every
                        frame; transform composites on the GPU. The label sits
                        OUTSIDE the scaled fill so it never distorts. */}
                    <div
                      className="relative flex h-full items-center rounded-full px-3"
                      style={{ width: `${widthPct}%` }}
                    >
                      <div
                        className="absolute inset-0 rounded-full"
                        style={{
                          backgroundColor: stage.color,
                          transform: animated ? 'scaleX(1)' : 'scaleX(0)',
                          transformOrigin: 'left',
                          transition: 'transform 0.8s cubic-bezier(0.22, 1, 0.36, 1)',
                        }}
                      />
                      {widthPct > 30 && (
                        <span className="relative text-xs font-semibold text-white">
                          {stage.count}
                        </span>
                      )}
                    </div>
                  </div>
                  {widthPct <= 30 && (
                    <span className="shrink-0 text-xs font-semibold">
                      {stage.count}
                    </span>
                  )}
                  {dropOffPct !== null && (
                    <span className="shrink-0 text-[11px] text-muted-foreground">
                      → {dropOffPct}%
                    </span>
                  )}
                </div>
              </div>
            )
          })}
        </div>

        <div className="mt-4 flex items-center justify-between border-t pt-3 text-xs text-muted-foreground">
          <span>
            Conversion: {convertedStage?.count ?? 0}/{firstStage?.count ?? 0} ={' '}
            {conversionPct}%
          </span>
          <span>
            Lost: {lostStage?.count ?? 0} ({lostPct}%)
          </span>
        </div>
      </CardContent>
    </Card>
  )
}
