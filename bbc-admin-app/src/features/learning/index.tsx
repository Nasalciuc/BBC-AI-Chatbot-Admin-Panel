import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { listQuestionVariants, setVariantStatus, getLearningReport } from '@/lib/api'
import type { QuestionVariant } from '@/lib/types'
import { HeaderActions } from '@/components/header-actions'
import { PageHeader } from '@/components/layout/page-header'

// The palette's green and amber pairs (styles/theme.css): each token carries
// its own dark value.
const STATUS_STYLES: Record<string, string> = {
  active: 'bg-k-e3f6ea text-k-0a6b35',
  proposed: 'bg-k-fff3e0 text-k-8a4b00',
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
    <main id='content' className='flex flex-col gap-[18px] px-4 pt-6 pb-9 md:px-7 md:pt-7'>
      {/* The header every page has: on a phone its menu button is the only
          way to the other pages, and the bell is where a new chat shows. */}
      <PageHeader
        title='Learning'
        subtitle={
          <>
            Question phrasings the bot is testing, and how treatment compares to the 10% control group. Nothing reaches
            customers until you set a variant to <strong>active</strong>.
          </>
        }
        actions={<HeaderActions className='ms-0' />}
      />

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
            {/* Six columns do not fit a phone: the table scrolls inside its
                card, the page does not move sideways. */}
            <div className='overflow-x-auto'>
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
                        {/* One change at a time: a second click while the first is
                            on its way would send it again, or the opposite one. */}
                        {v.status !== 'active' && (
                          <button type="button" disabled={mutate.isPending} className="text-xs text-k-0a6b35 hover:underline mr-3 disabled:opacity-50" onClick={() => mutate.mutate({ id: v.id, status: 'active' })}>Activate</button>
                        )}
                        {v.status !== 'retired' && (
                          <button type="button" disabled={mutate.isPending} className="text-xs text-muted-foreground hover:underline disabled:opacity-50" onClick={() => mutate.mutate({ id: v.id, status: 'retired' })}>Retire</button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))
      )}
    </main>
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
