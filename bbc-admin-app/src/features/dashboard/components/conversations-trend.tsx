/**
 * Conversations trend area chart — 14 days, sales vs support split.
 * Data source: DashboardStats.conversations_trend_v2 fetched from GET /api/dashboard/stats (FastAPI backend)
 * Parent: src/features/dashboard/index.tsx passes conversations_trend_v2 array as props
 */

import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from 'recharts'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'

interface ConversationsTrendProps {
  conversations_trend_v2: Array<{
    date: string
    sales: number
    support: number
  }>
}

export function ConversationsTrend({
  conversations_trend_v2 = [],
}: ConversationsTrendProps) {
  if (conversations_trend_v2.length === 0) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Conversations Trend</CardTitle>
          <CardDescription>Last 14 days · Sales vs Support</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex h-[260px] items-center justify-center text-sm text-muted-foreground">
            No trend data yet
          </div>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Conversations Trend</CardTitle>
        <CardDescription>Last 14 days · Sales vs Support</CardDescription>
      </CardHeader>
      <CardContent>
        <ResponsiveContainer width="100%" height={260}>
          <AreaChart
            data={conversations_trend_v2}
            margin={{ top: 5, right: 5, bottom: 5, left: -10 }}
          >
            <defs>
              <linearGradient
                id="salesGradient"
                x1="0"
                y1="0"
                x2="0"
                y2="1"
              >
                <stop offset="5%" stopColor="#C9A54E" stopOpacity={0.3} />
                <stop offset="95%" stopColor="#C9A54E" stopOpacity={0} />
              </linearGradient>
              <linearGradient
                id="supportGradient"
                x1="0"
                y1="0"
                x2="0"
                y2="1"
              >
                <stop offset="5%" stopColor="#0B1829" stopOpacity={0.15} />
                <stop offset="95%" stopColor="#0B1829" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid
              strokeDasharray="3 3"
              stroke="#f0f0f0"
              vertical={false}
            />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 11, fill: '#9ca3af' }}
              tickLine={false}
              axisLine={false}
              tickFormatter={(d: string) => {
                const date = new Date(d)
                return date.toLocaleDateString('en-US', {
                  month: 'short',
                  day: 'numeric',
                })
              }}
            />
            <YAxis
              tick={{ fontSize: 11, fill: '#9ca3af' }}
              tickLine={false}
              axisLine={false}
              allowDecimals={false}
            />
            <Tooltip
              contentStyle={{
                borderRadius: 8,
                border: '1px solid #e5e7eb',
                boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)',
              }}
              labelFormatter={(d: any) =>
                new Date(d).toLocaleDateString('en-US', {
                  weekday: 'short',
                  month: 'short',
                  day: 'numeric',
                })
              }
            />
            <Area
              type="monotone"
              dataKey="sales"
              name="Sales"
              stroke="#C9A54E"
              strokeWidth={2}
              fill="url(#salesGradient)"
              dot={false}
              activeDot={{ r: 4, fill: '#C9A54E' }}
            />
            <Area
              type="monotone"
              dataKey="support"
              name="Support"
              stroke="#0B1829"
              strokeWidth={2}
              fill="url(#supportGradient)"
              dot={false}
              activeDot={{ r: 4, fill: '#0B1829' }}
            />
            <Legend
              iconType="circle"
              iconSize={8}
              wrapperStyle={{ paddingTop: 12, fontSize: 12 }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  )
}
