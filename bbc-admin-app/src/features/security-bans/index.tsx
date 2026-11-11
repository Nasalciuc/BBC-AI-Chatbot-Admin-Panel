import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { listBans, unbanBan, type SecurityBan } from '@/lib/api'
import { HeaderActions } from '@/components/header-actions'
import { PageHeader } from '@/components/layout/page-header'

/** What /api/admin/security/bans says when its database read fails. */
const HISTORY_READ_FAILED = 'Bans history unavailable'

function isActive(b: SecurityBan): boolean {
  if (b.unbanned_at) return false
  return !b.expires_at || new Date(b.expires_at).getTime() > Date.now()
}

export function SecurityBans() {
  const qc = useQueryClient()
  const bans = useQuery({ queryKey: ['security-bans'], queryFn: listBans })
  const unban = useMutation({
    mutationFn: (id: string) => unbanBan(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['security-bans'] }); toast.success('Unbanned') },
    onError: () => toast.error('Unban failed'),
  })

  return (
    <main id='content' className='flex flex-col gap-[18px] px-4 pt-6 pb-9 md:px-7 md:pt-7'>
      {/* The header every page has: on a phone its menu button is the only
          way to the other pages, and the bell is where a new chat shows. */}
      <PageHeader
        title='Security · Bans'
        subtitle='Addresses the tripwires banned. A ban lasts 1 hour to a week and doubles on a repeat; none is permanent. Unban lets the next request through and records who did it.'
        actions={<HeaderActions className='ms-0' />}
      />
      {bans.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : bans.isError ? (
        // What actually failed (a sign-in that ran out, "Forbidden", the
        // network): migration 042 is one cause of the server's own failure,
        // not of every one.
        <p className="text-sm text-muted-foreground">
          Couldn’t load the ban history: {bans.error instanceof Error ? bans.error.message : 'unknown error'}.
          {bans.error instanceof Error && bans.error.message === HISTORY_READ_FAILED
            ? ' One cause: migration 042 not applied yet.'
            : null}
        </p>
      ) : (bans.data ?? []).length === 0 ? (
        <p className="text-sm text-muted-foreground">No bans recorded.</p>
      ) : (
        // Six columns do not fit a phone: the table scrolls inside its card,
        // the page does not move sideways.
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead className="text-muted-foreground">
              <tr className="text-left">
                <th className="px-4 py-2 font-medium">Identity</th>
                <th className="px-4 py-2 font-medium">Jail</th>
                <th className="px-4 py-2 font-medium">Last seen</th>
                <th className="px-4 py-2 font-medium">Expires</th>
                <th className="px-4 py-2 font-medium">State</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody>
              {(bans.data ?? []).map((b) => (
                <tr key={b.id} className="border-t border-border">
                  <td className="px-4 py-2 font-mono text-foreground">{b.identity}</td>
                  <td className="px-4 py-2">{b.jail}</td>
                  <td className="px-4 py-2">{fmtTime(b.last_seen_at)}</td>
                  <td className="px-4 py-2">{fmtTime(b.expires_at)}</td>
                  <td className="px-4 py-2">
                    {isActive(b) ? 'Active' : b.unbanned_at ? `Unbanned by ${b.unbanned_by ?? '—'}` : 'Expired'}
                  </td>
                  <td className="px-4 py-2 text-right">
                    {isActive(b) && (
                      <button
                        data-testid={`unban-${b.id}`}
                        className="text-xs text-foreground hover:underline"
                        disabled={unban.isPending}
                        onClick={() => unban.mutate(b.id)}
                      >Unban</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  )
}

function fmtTime(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString()
}
