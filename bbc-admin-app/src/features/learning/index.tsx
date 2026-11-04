import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { listQuestionVariants, setVariantStatus, getLearningReport } from '@/lib/api'
import type { QuestionVariant } from '@/lib/types'

const STATUS_STYLES: Record<string, string> = {
  active: 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200',
  proposed: 'bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200',
  retired: 'bg-muted text-muted-foreground',
}

export function Learning() {
  const qc = useQueryClient()
  const variants = useQuery({ queryKey: ['question-variants'], queryFn: listQuestionVariants })
  const report = useQuery({ queryKey: ['learning-report'], queryFn: getLearningReport })

  const mutate = useMutation({
    mutationFn: ({ id, status }: { id: string; status: QuestionVariant['status'] }) => setVariantStatus(id, status),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['question-variants'] }); toast.success('Updated') },
    onError: () => toast.error('Update failed'),
  })

  const bySlot: Record<string, QuestionVariant[]> = {}
  for (const v of variants.data ?? []) (bySlot[v.slot] ??= []).push(v)

  return (
    <div className="h-full overflow-y-auto p-6 space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-foreground">Learning</h1>
        <p className="text-sm text-muted-foreground">
          Question phrasings the bot is testing, and how treatment compares to the 10% control group.
          Nothing reaches customers until you set a variant to <strong>active</strong>.
        </p>
      </div>

      {report.isError && (
        <LoadError what="the learning report" onRetry={() => report.refetch()} />
      )}

      {report.data && (
        <div className="rounded-lg border border-border p-4 text-sm">
          <h2 className="font-semibold mb-2 text-foreground">Complete-detail answer rate</h2>
          <div className="flex gap-8">
            <div>Treatment: <strong>{fmt(report.data.treatment_answer_rate)}</strong> <span className="text-muted-foreground">(n={report.data.treatment_n})</span></div>
            <div>Control: <strong>{fmt(report.data.control_answer_rate)}</strong> <span className="text-muted-foreground">(n={report.data.control_n})</span></div>
          </div>
        </div>
      )}

      {variants.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : variants.isError ? (
        // A failed read is not "no variants": say so and offer a retry.
        <LoadError what="the question variants" onRetry={() => variants.refetch()} />
      ) : Object.keys(bySlot).length === 0 ? (
        <p className="text-sm text-muted-foreground">No question variants yet. The daily loop proposes them.</p>
      ) : (
        Object.entries(bySlot).map(([slot, vs]) => (
          <div key={slot} className="rounded-lg border border-border">
            <div className="px-4 py-2 border-b border-border font-medium text-foreground">{slot}</div>
            <table className="w-full text-sm">
              <thead className="text-muted-foreground">
                <tr className="text-left">
                  <th className="px-4 py-2 font-medium">Question</th>
                  <th className="px-4 py-2 font-medium">Shows</th>
                  <th className="px-4 py-2 font-medium">Answer rate</th>
                  <th className="px-4 py-2 font-medium">P(best)</th>
                  <th className="px-4 py-2 font-medium">Status</th>
                  <th className="px-4 py-2" />
                </tr>
              </thead>
              <tbody>
                {vs.map((v) => (
                  <tr key={v.id} className="border-t border-border">
                    <td className="px-4 py-2 text-foreground">{v.text}</td>
                    <td className="px-4 py-2">{v.shows}</td>
                    <td className="px-4 py-2">{fmt(v.answer_rate)}</td>
                    <td className="px-4 py-2">{v.prob_best?.toFixed(2) ?? '—'}</td>
                    <td className="px-4 py-2">
                      <span className={`inline-flex px-2 py-0.5 rounded text-xs font-medium ${STATUS_STYLES[v.status] ?? ''}`}>{v.status}</span>
                    </td>
                    <td className="px-4 py-2 text-right whitespace-nowrap">
                      {v.status !== 'active' && (
                        <button className="text-xs text-green-700 hover:underline mr-3" onClick={() => mutate.mutate({ id: v.id, status: 'active' })}>Activate</button>
                      )}
                      {v.status !== 'retired' && (
                        <button className="text-xs text-muted-foreground hover:underline" onClick={() => mutate.mutate({ id: v.id, status: 'retired' })}>Retire</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))
      )}
    </div>
  )
}

function LoadError({ what, onRetry }: { what: string; onRetry: () => void }) {
  return (
    <div role="alert" className="rounded-lg border border-destructive/40 p-4 text-sm text-destructive">
      Could not load {what}.{' '}
      <button type="button" className="underline" onClick={onRetry}>Try again</button>
    </div>
  )
}

function fmt(r: number | null | undefined): string {
  return r == null ? '—' : `${Math.round(r * 100)}%`
}
