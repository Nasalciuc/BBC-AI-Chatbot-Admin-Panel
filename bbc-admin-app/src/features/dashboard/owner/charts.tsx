import { cn } from '@/lib/utils'
import type { HourRow, OwnerDashboard } from '@/lib/types'
import { dayTick, hourLong, hourTick } from './view'

type DayBar = OwnerDashboard['overview']['by_day'][number]

const SERIES = [
  { key: 'ready', label: 'Ready to quote', cls: 'bg-k-2a78d6' },
  { key: 'other', label: 'Other leads', cls: 'bg-k-1baf7a' },
  { key: 'contact_only', label: 'Contact only', cls: 'bg-k-eb6834' },
] as const

export function Legend({ items }: { items: readonly { label: string; cls: string }[] }) {
  return (
    <ul className='m-0 flex list-none flex-wrap gap-x-4 gap-y-1 p-0 text-[13px] text-k-444444'>
      {items.map((i) => (
        <li key={i.label} className='flex items-center gap-1.5'>
          <span aria-hidden='true' className={cn('size-2.5 rounded-[3px]', i.cls)} />
          {i.label}
        </li>
      ))}
    </ul>
  )
}

/** Chats by day, stacked: ready, other leads, contact only. */
export function DayBars({ bars, today, height = 200 }: { bars: DayBar[]; today: string; height?: number }) {
  const total = (b: DayBar) => b.ready + b.other + b.contact_only
  const max = Math.max(1, ...bars.map(total))
  const dense = bars.length > 14
  return (
    <div className='flex flex-col gap-3'>
      <Legend items={SERIES} />
      <div className='flex items-end gap-[3%] md:gap-3' style={{ height: height + 24 }} aria-hidden='true'>
        {bars.map((b) => {
          const t = total(b)
          return (
            <div
              key={b.date}
              title={`${dayTick(b.date, true)}: ${t} chats · ${b.ready} ready to quote · ${b.other} other leads · ${b.contact_only} contact only`}
              className='flex h-full min-w-0 flex-1 flex-col justify-end'
            >
              {!dense && <span className='mb-1 text-center text-[11px] text-k-555555'>{t}</span>}
              <div className='flex flex-col-reverse gap-px overflow-hidden rounded-[4px]' style={{ height: `${(t / max) * height}px` }}>
                {SERIES.map((s) =>
                  b[s.key] > 0 ? <div key={s.key} className={s.cls} style={{ flex: b[s.key] }} /> : null
                )}
              </div>
            </div>
          )
        })}
      </div>
      <div className='flex gap-[3%] md:gap-3' aria-hidden='true'>
        {bars.map((b, i) => (
          <span
            key={b.date}
            className={cn('min-w-0 flex-1 truncate text-center text-[11px] text-k-6b6b6b', b.date === today && 'font-bold text-k-202020')}
          >
            {dense ? (i % 5 === 0 || b.date === today ? String(new Date(`${b.date}T12:00:00`).getDate()) : '') : dayTick(b.date, true)}
          </span>
        ))}
      </div>
      <table className='sr-only'>
        <caption>Chats by day</caption>
        <thead>
          <tr>
            <th>Day</th>
            <th>Ready to quote</th>
            <th>Other leads</th>
            <th>Contact only</th>
          </tr>
        </thead>
        <tbody>
          {bars.map((b) => (
            <tr key={b.date}>
              <td>{dayTick(b.date, true)}</td>
              <td>{b.ready}</td>
              <td>{b.other}</td>
              <td>{b.contact_only}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

const HOUR_SERIES = [
  { label: 'Answered by the agent', cls: 'bg-k-202020' },
  { label: 'Went back to the assistant', cls: 'bg-k-d93025' },
] as const

/** The hours that have anything, from the first to the last (at least 8a–11p). */
function hourWindow(rows: HourRow[]): HourRow[] {
  const used = rows.filter((r) => r.given > 0 || r.ready_people > 0).map((r) => r.hour)
  const lo = Math.min(8, ...used)
  const hi = Math.max(23, ...used)
  return rows.filter((r) => r.hour >= lo && r.hour <= hi)
}

export function HourBars({ rows, worst, height = 150 }: { rows: HourRow[]; worst: number | null; height?: number }) {
  const shown = hourWindow(rows)
  const max = Math.max(1, ...shown.map((r) => r.given))
  return (
    <div className='flex flex-col gap-3'>
      <Legend items={HOUR_SERIES} />
      <div className='overflow-x-auto'>
        <div className='flex min-w-[560px] flex-col gap-1.5'>
          <div className='flex items-end gap-1.5' style={{ height: height + 20 }} aria-hidden='true'>
            {shown.map((r) => (
              <div
                key={r.hour}
                title={`${hourLong(r.hour)}: ${r.given} given, ${r.went_back} went back, ${r.ready_people} Ready`}
                className='flex h-full min-w-0 flex-1 flex-col justify-end'
              >
                {r.given > 0 && (
                  <span className={cn('mb-1 text-center text-[11px]', worst === r.hour ? 'font-bold text-k-b3261e' : 'text-k-555555')}>{r.given}</span>
                )}
                <div className='flex flex-col-reverse gap-px overflow-hidden rounded-[4px]' style={{ height: `${(r.given / max) * height}px` }}>
                  {r.given - r.went_back > 0 && <div className='bg-k-202020' style={{ flex: r.given - r.went_back }} />}
                  {r.went_back > 0 && <div className='bg-k-d93025' style={{ flex: r.went_back }} />}
                </div>
              </div>
            ))}
          </div>
          <div className='flex gap-1.5' aria-hidden='true'>
            {shown.map((r) => (
              <span key={r.hour} className={cn('min-w-0 flex-1 text-center text-[11px] text-k-6b6b6b', worst === r.hour && 'font-bold text-k-b3261e')}>
                {hourTick(r.hour)}
              </span>
            ))}
          </div>
          <div className='flex gap-1.5' aria-hidden='true'>
            {shown.map((r) => (
              <span
                key={r.hour}
                className={cn(
                  'min-w-0 flex-1 rounded-md py-0.5 text-center text-[11px] font-bold',
                  worst === r.hour ? 'bg-k-fdecea text-k-b3261e' : 'bg-k-f2f2f2 text-k-444444'
                )}
              >
                {r.ready_people}
              </span>
            ))}
          </div>
        </div>
      </div>
      <table className='sr-only'>
        <caption>By hour</caption>
        <thead>
          <tr>
            <th>Hour</th>
            <th>Given to agents</th>
            <th>Went back to the assistant</th>
            <th>People Ready</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((r) => (
            <tr key={r.hour}>
              <td>{hourLong(r.hour)}</td>
              <td>{r.given}</td>
              <td>{r.went_back}</td>
              <td>{r.ready_people}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
