import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Search } from 'lucide-react'
import { toast } from 'sonner'
import { type ApiError, getAccessLog, getPanelUsers, inviteUser, updateUser } from '@/lib/api'
import type { PanelUser } from '@/lib/types'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth-store'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { HeaderActions } from '@/components/header-actions'
import { PageHeader } from '@/components/layout/page-header'
import { PersonAvatar } from '@/components/person-avatar'
import { InviteDialog } from './components/invite-dialog'
import { PersonPanel } from './components/person-panel'
import {
  CAPABILITIES,
  LOG_DOT,
  ROLES_NOTE,
  ROLE_LABEL,
  ROLE_ORDER,
  TUNNEL_LABEL,
  accessSentence,
  avatarTone,
  isInvite,
  lastActive,
  logFilterOf,
  logWhen,
  statusOf,
  tunnelFor,
  type LogFilter,
  type StatusKey,
} from './view'

type Tab = 'people' | 'invites' | 'roles' | 'log'
type StatusFilter = 'all' | 'active' | 'invites' | 'deactivated'

const tabBtn = (on: boolean) =>
  cn(
    'flex h-10 items-center gap-2 rounded-xl px-4 text-[15px] focus-visible:outline-2 focus-visible:outline-k-6ea8ff',
    on ? 'bg-k-ffffff font-bold text-k-202020 shadow-sm' : 'text-k-444444'
  )

function sortPeople(list: PanelUser[], nowMs: number): PanelUser[] {
  // Active first, then invites, then deactivated; inside each, owners and admins first.
  const rank = (s: StatusKey) => (s === 'active' ? 0 : s === 'deactivated' ? 2 : 1)
  const order = ['owner', 'admin', 'dev', 'project_manager', 'supervisor', 'qa', 'sales', 'support']
  return [...list].sort(
    (a, b) =>
      rank(statusOf(a, nowMs).key) - rank(statusOf(b, nowMs).key) ||
      order.indexOf(a.role) - order.indexOf(b.role) ||
      (a.name ?? a.email).localeCompare(b.name ?? b.email)
  )
}

/**
 * Users & access: who can sign in to the panel, and what each person can do.
 * Owner, admin and dev change access; the backend enforces the same rules.
 */
export function Users() {
  const me = useAuthStore((s) => s.auth.user)
  const myRole = me?.role ?? ''
  const qc = useQueryClient()
  const canManage = ['owner', 'admin', 'dev'].includes(myRole)
  const [tab, setTab] = useState<Tab>('people')
  const [search, setSearch] = useState('')
  const [roleFilter, setRoleFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [logFilter, setLogFilter] = useState<LogFilter>('all')
  const [openId, setOpenId] = useState<string | null>(null)
  const [inviting, setInviting] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [confirmBulk, setConfirmBulk] = useState(false)
  const [nowMs, setNowMs] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNowMs(Date.now()), 60_000)
    return () => clearInterval(t)
  }, [])

  const users = useQuery({ queryKey: ['panel-users'], queryFn: getPanelUsers })
  const log = useQuery({ queryKey: ['access-log'], queryFn: () => getAccessLog(200), enabled: canManage })
  const everyone = useMemo(() => users.data ?? [], [users.data])
  const statuses = useMemo(() => new Map(everyone.map((u) => [u.id, statusOf(u, nowMs)])), [everyone, nowMs])
  const stOf = (u: PanelUser) => statuses.get(u.id) ?? statusOf(u, nowMs)

  const counts = useMemo(() => {
    let active = 0
    let invites = 0
    let off = 0
    for (const u of everyone) {
      const k = (statuses.get(u.id) ?? statusOf(u, nowMs)).key
      if (k === 'active') active++
      else if (k === 'deactivated') off++
      else invites++
    }
    return { all: everyone.length, active, invites, deactivated: off }
  }, [everyone, statuses, nowMs])

  const people = useMemo(() => {
    const q = search.trim().toLowerCase()
    return sortPeople(
      everyone.filter((u) => {
        const k = (statuses.get(u.id) ?? statusOf(u, nowMs)).key
        if (tab === 'invites' && !isInvite(k)) return false
        if (tab === 'people') {
          if (statusFilter === 'active' && k !== 'active') return false
          if (statusFilter === 'invites' && !isInvite(k)) return false
          if (statusFilter === 'deactivated' && k !== 'deactivated') return false
        }
        if (roleFilter && u.role !== roleFilter) return false
        if (q && !`${u.name ?? ''} ${u.email}`.toLowerCase().includes(q)) return false
        return true
      }),
      nowMs
    )
  }, [everyone, statuses, tab, statusFilter, roleFilter, search, nowMs])

  const roleCounts = useMemo(() => {
    const m: Record<string, number> = {}
    for (const u of everyone) if (u.is_active) m[u.role] = (m[u.role] ?? 0) + 1
    return m
  }, [everyone])

  const sel = people.filter((u) => selected.has(u.id))
  const selInvites = sel.filter((u) => isInvite(stOf(u).key))
  const selOff = sel.filter((u) => stOf(u).key !== 'deactivated' && u.id !== me?.accountNo && !(u.role === 'owner' && myRole !== 'owner'))
  const toggleSel = (id: string) =>
    setSelected((s) => {
      const n = new Set(s)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })

  const bulkResend = useMutation({
    mutationFn: async () => {
      let ok = 0
      for (const u of selInvites) {
        await inviteUser({ name: u.name || u.email, email: u.email, role: u.role, tunnel_scope: u.tunnel_scope || tunnelFor(u.role) })
        ok++
      }
      return ok
    },
    onSuccess: (n) => {
      toast.success(`${n === 1 ? '1 invite' : `${n} invites`} sent again. Each link works for 48 hours.`)
      setSelected(new Set())
      qc.invalidateQueries({ queryKey: ['panel-users'] })
      qc.invalidateQueries({ queryKey: ['access-log'] })
    },
    onError: (e) => {
      toast.error((e as ApiError)?.message ?? 'Some invites didn’t go out. Check the list and try again.')
      qc.invalidateQueries({ queryKey: ['panel-users'] })
    },
  })
  const bulkOff = useMutation({
    mutationFn: async () => {
      let ok = 0
      for (const u of selOff) {
        await updateUser(u.id, { is_active: false })
        ok++
      }
      return ok
    },
    onSuccess: (n) => {
      toast.success(`${n === 1 ? '1 person' : `${n} people`} deactivated.`)
      setSelected(new Set())
      setConfirmBulk(false)
      qc.invalidateQueries({ queryKey: ['panel-users'] })
      qc.invalidateQueries({ queryKey: ['access-log'] })
    },
    onError: (e) => {
      toast.error((e as ApiError)?.message ?? 'Some couldn’t be deactivated. Check the list.')
      setConfirmBulk(false)
      qc.invalidateQueries({ queryKey: ['panel-users'] })
    },
  })

  const openUser = everyone.find((u) => u.id === openId) ?? null
  const logRows = (log.data ?? []).filter((e) => logFilter === 'all' || logFilterOf(e) === logFilter)

  return (
    <main id='content' className='flex flex-col gap-4 px-4 pt-6 pb-9 md:px-7 md:pt-7'>
      <PageHeader
        title='Users & access'
        subtitle='Who can sign in to the chat panel, and what each person can do.'
        actions={
          <div className='flex items-center gap-2.5'>
            {canManage && (
              <button type='button' onClick={() => setInviting(true)} className='h-11 rounded-full bg-kb-202020 px-5 text-[15px] font-bold text-on-btn'>
                Invite people
              </button>
            )}
            <HeaderActions className='ms-0' />
          </div>
        }
      />

      <div role='tablist' aria-label='Users & access' className='flex w-fit max-w-full flex-wrap gap-1 rounded-2xl bg-k-f2f2f2 p-1'>
        <button type='button' role='tab' aria-selected={tab === 'people'} onClick={() => setTab('people')} className={tabBtn(tab === 'people')}>
          People <span className='rounded-full bg-k-e9e9e9 px-1.5 text-xs font-bold text-k-444444'>{counts.all - counts.deactivated}</span>
        </button>
        <button type='button' role='tab' aria-selected={tab === 'invites'} onClick={() => setTab('invites')} className={tabBtn(tab === 'invites')}>
          Invites
          <span className={cn('rounded-full px-1.5 text-xs font-bold', counts.invites ? 'bg-k-fdecea text-k-b3261e' : 'bg-k-e9e9e9 text-k-444444')}>
            {counts.invites}
          </span>
        </button>
        <button type='button' role='tab' aria-selected={tab === 'roles'} onClick={() => setTab('roles')} className={tabBtn(tab === 'roles')}>
          Roles
        </button>
        {canManage && (
          <button type='button' role='tab' aria-selected={tab === 'log'} onClick={() => setTab('log')} className={tabBtn(tab === 'log')}>
            Access log
          </button>
        )}
      </div>

      {(tab === 'people' || tab === 'invites') && (
        <>
          <div className='flex flex-wrap items-center gap-2'>
            <label className='relative min-w-[220px] flex-1 sm:max-w-[300px]'>
              <span className='sr-only'>Search people</span>
              <Search aria-hidden='true' className='pointer-events-none absolute start-3 top-1/2 size-4 -translate-y-1/2 text-k-6b6b6b' />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder='Search a name or e-mail'
                className='h-10 w-full rounded-xl bg-k-f2f2f2 ps-9 pe-3 text-sm text-k-202020 placeholder:text-k-6b6b6b'
              />
            </label>
            <label className='flex items-center gap-1.5 text-[13px] text-k-555555'>
              Role
              <select
                value={roleFilter}
                onChange={(e) => setRoleFilter(e.target.value)}
                className='h-10 rounded-xl border border-k-d9d9d9 bg-k-ffffff px-3 text-sm text-k-202020'
              >
                <option value=''>All roles</option>
                {ROLE_ORDER.map((r) => (
                  <option key={r} value={r}>
                    {ROLE_LABEL[r]}
                  </option>
                ))}
              </select>
            </label>
            {tab === 'people' && (
              <div role='radiogroup' aria-label='Status' className='ms-auto flex flex-wrap gap-1 rounded-2xl bg-k-f2f2f2 p-1'>
                {(
                  [
                    ['all', 'All', counts.all],
                    ['active', 'Active', counts.active],
                    ['invites', 'Invites', counts.invites],
                    ['deactivated', 'Deactivated', counts.deactivated],
                  ] as [StatusFilter, string, number][]
                ).map(([k, label, n]) => (
                  <button
                    key={k}
                    type='button'
                    role='radio'
                    aria-checked={statusFilter === k}
                    onClick={() => setStatusFilter(k)}
                    className={cn('h-8 rounded-xl px-3 text-[13px]', statusFilter === k ? 'bg-k-ffffff font-bold text-k-202020 shadow-sm' : 'text-k-444444')}
                  >
                    {label} <span className='text-k-6b6b6b'>{n}</span>
                  </button>
                ))}
              </div>
            )}
          </div>

          {canManage && sel.length > 0 && (
            <div className='flex flex-wrap items-center gap-2 rounded-2xl bg-kb-202020 px-4 py-2.5 text-on-btn'>
              <span className='me-auto text-sm font-bold'>{sel.length} selected</span>
              {selInvites.length > 0 && (
                <button
                  type='button'
                  disabled={bulkResend.isPending}
                  onClick={() => bulkResend.mutate()}
                  className='h-8 rounded-full bg-k-ffffff px-3.5 text-[13px] font-bold text-k-202020'
                >
                  Resend invites ({selInvites.length})
                </button>
              )}
              {selOff.length > 0 && (
                <button type='button' onClick={() => setConfirmBulk(true)} className='h-8 rounded-full bg-k-ffffff px-3.5 text-[13px] font-bold text-k-b3261e'>
                  Deactivate ({selOff.length})
                </button>
              )}
              <button type='button' onClick={() => setSelected(new Set())} className='h-8 px-2 text-[13px] text-on-btn opacity-80'>
                Clear
              </button>
            </div>
          )}

          {users.isError ? (
            <div role='alert' className='flex flex-wrap items-center gap-3 rounded-2xl bg-k-fff3e0 px-4 py-3 text-sm text-k-8a4b00'>
              <span className='flex-1'>
                <strong>Couldn’t load the people.</strong> {(users.error as ApiError)?.status === 403 ? 'Your role can’t see this list.' : 'Try again.'}
              </span>
              <button type='button' onClick={() => users.refetch()} className='h-9 rounded-full bg-kb-202020 px-4 text-[13px] font-bold text-on-btn'>
                Try again
              </button>
            </div>
          ) : users.isLoading ? (
            <div className='h-72 animate-pulse rounded-[20px] bg-k-f7f7f7' aria-busy='true' />
          ) : people.length === 0 ? (
            <div className='rounded-[20px] px-5 py-10 text-center text-sm text-k-555555 ring-1 ring-k-ececec'>
              {tab === 'invites' ? 'No invite is waiting.' : 'Nobody matches. Clear the search or the filters.'}
            </div>
          ) : (
            <div className='overflow-x-auto rounded-[20px] ring-1 ring-k-ececec'>
              <table className='w-full min-w-[820px] border-collapse'>
                <thead>
                  <tr className='bg-k-f7f7f7'>
                    {canManage && <th className='w-12 px-4' />}
                    {['Person', 'Role', 'Tunnel', 'Status', 'Last active'].map((h) => (
                      <th key={h} className='px-4 py-3 text-left text-[11px] font-bold tracking-[0.05em] text-k-555555 uppercase'>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {people.map((u) => {
                    const st = stOf(u)
                    const isMe = u.id === me?.accountNo
                    return (
                      <tr
                        key={u.id}
                        tabIndex={0}
                        onClick={() => setOpenId(u.id)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') setOpenId(u.id)
                        }}
                        className='cursor-pointer border-t border-k-ececec hover:bg-row-hover focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-k-6ea8ff'
                      >
                        {canManage && (
                          <td className='px-4 py-2.5' onClick={(e) => e.stopPropagation()}>
                            <input
                              type='checkbox'
                              aria-label={`Select ${u.name ?? u.email}`}
                              checked={selected.has(u.id)}
                              disabled={isMe}
                              onChange={() => toggleSel(u.id)}
                              className='size-[18px] accent-current'
                            />
                          </td>
                        )}
                        <td className='px-4 py-2.5'>
                          <div className='flex items-center gap-3'>
                            <PersonAvatar name={u.name || u.email} tone={avatarTone(u, st.key)} size={36} />
                            <div className='min-w-0'>
                              <strong className='block truncate text-[15px] text-k-202020'>
                                {u.name || u.email}
                                {isMe && <span className='ms-1 font-normal text-k-6b6b6b'>(you)</span>}
                              </strong>
                              <span className='block truncate text-xs text-k-555555'>{u.email}</span>
                            </div>
                          </div>
                        </td>
                        <td className='px-4 py-2.5 text-sm text-k-202020'>{ROLE_LABEL[u.role] ?? u.role}</td>
                        <td className='px-4 py-2.5 text-sm text-k-202020'>{TUNNEL_LABEL[u.tunnel_scope ?? ''] ?? '—'}</td>
                        <td className='px-4 py-2.5'>
                          <span className={cn('rounded-full px-2 py-0.5 text-xs font-bold', st.chip)}>{st.label}</span>
                          {st.sub && <span className={cn('mt-0.5 block text-xs', st.subCls)}>{st.sub}</span>}
                        </td>
                        <td className='px-4 py-2.5 text-sm text-k-202020'>
                          {st.key === 'deactivated' ? lastActive(u.last_seen_at, nowMs).replace('Never signed in', '—') : lastActive(u.last_seen_at, nowMs)}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {tab === 'roles' && (
        <>
          <p className='m-0 max-w-[820px] text-sm leading-[1.5] text-k-555555'>{ROLES_NOTE}</p>
          <div className='overflow-x-auto rounded-[20px] ring-1 ring-k-ececec'>
            <table className='w-full min-w-[980px] border-collapse'>
              <thead>
                <tr className='bg-k-f7f7f7'>
                  <th className='px-4 py-3 text-left align-bottom text-[11px] font-bold tracking-[0.05em] text-k-555555 uppercase'>Can…</th>
                  {ROLE_ORDER.map((r) => (
                    <th key={r} className='px-2 py-3 text-center align-bottom'>
                      <strong className='block text-[13px] text-k-202020'>{ROLE_LABEL[r].replace(' agent', '')}</strong>
                      <span className='text-xs font-normal text-k-6b6b6b'>
                        {roleCounts[r] ?? 0} {(roleCounts[r] ?? 0) === 1 ? 'person' : 'people'}
                      </span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {CAPABILITIES.map((c) => (
                  <tr key={c.label} className='border-t border-k-ececec'>
                    <td className='px-4 py-2.5'>
                      <span className='block text-sm text-k-202020'>{c.label}</span>
                      {c.sub && <span className='block text-xs text-k-6b6b6b'>{c.sub}</span>}
                    </td>
                    {ROLE_ORDER.map((r) => {
                      const v = c.cells[r]
                      return (
                        <td key={r} className='px-2 py-2.5 text-center text-[13px] text-k-202020'>
                          {v === 'y' ? (
                            <Check aria-label='Yes' className='mx-auto size-4 text-k-0a6b35' />
                          ) : v === 'n' ? (
                            <span aria-label='No' className='text-k-b5b5b5'>
                              —
                            </span>
                          ) : (
                            v
                          )}
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {tab === 'log' && canManage && (
        <>
          <div className='flex flex-wrap items-center gap-3'>
            <div role='radiogroup' aria-label='Show' className='flex flex-wrap gap-1 rounded-2xl bg-k-f2f2f2 p-1'>
              {(
                [
                  ['all', 'All'],
                  ['roles', 'Roles'],
                  ['invites', 'Invites'],
                  ['status', 'Deactivations'],
                  ['chats', 'Receives chats'],
                ] as [LogFilter, string][]
              ).map(([k, label]) => (
                <button
                  key={k}
                  type='button'
                  role='radio'
                  aria-checked={logFilter === k}
                  onClick={() => setLogFilter(k)}
                  className={cn('h-8 rounded-xl px-3 text-[13px]', logFilter === k ? 'bg-k-ffffff font-bold text-k-202020 shadow-sm' : 'text-k-444444')}
                >
                  {label}
                </button>
              ))}
            </div>
            <p className='m-0 text-[13px] text-k-555555'>
              Every change to who can sign in and what they can do. Profile edits (name, phone, photo) and team moves aren’t recorded.
            </p>
          </div>
          {log.isError ? (
            <div role='alert' className='flex flex-wrap items-center gap-3 rounded-2xl bg-k-fff3e0 px-4 py-3 text-sm text-k-8a4b00'>
              <span className='flex-1'>
                <strong>Couldn’t load the access log.</strong>
              </span>
              <button type='button' onClick={() => log.refetch()} className='h-9 rounded-full bg-kb-202020 px-4 text-[13px] font-bold text-on-btn'>
                Try again
              </button>
            </div>
          ) : log.isLoading ? (
            <div className='h-64 animate-pulse rounded-[20px] bg-k-f7f7f7' aria-busy='true' />
          ) : logRows.length === 0 ? (
            <div className='rounded-[20px] px-5 py-10 text-center text-sm text-k-555555 ring-1 ring-k-ececec'>Nothing recorded here yet.</div>
          ) : (
            <ul className='m-0 flex list-none flex-col overflow-hidden rounded-[20px] p-0 ring-1 ring-k-ececec'>
              {logRows.map((e) => {
                const s = accessSentence(e)
                const when = logWhen(e.at, nowMs)
                return (
                  <li key={`${e.at}-${e.kind}-${e.target_id}`} className='flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-k-ececec px-4 py-3 first:border-t-0'>
                    <span className='w-[120px] flex-none text-[13px] text-k-555555'>{when}</span>
                    <span aria-hidden='true' className={cn('size-2 flex-none rounded-full', LOG_DOT[e.kind])} />
                    <span className='min-w-0 flex-1 text-sm text-k-202020'>
                      <strong>{s.actor}</strong> {s.text}
                    </span>
                    {e.target_id && everyone.some((u) => u.id === e.target_id) && (
                      <button
                        type='button'
                        onClick={() => setOpenId(e.target_id)}
                        className='h-8 rounded-full border border-k-d9d9d9 px-3 text-xs font-bold text-k-202020'
                      >
                        {e.target_name}
                      </button>
                    )}
                  </li>
                )
              })}
            </ul>
          )}
        </>
      )}

      <PersonPanel
        user={openUser}
        me={{ id: me?.accountNo ?? '', role: myRole }}
        canManage={canManage}
        everyone={everyone}
        log={canManage ? log.data : []}
        nowMs={nowMs}
        onClose={() => setOpenId(null)}
      />
      <InviteDialog open={inviting} onOpenChange={setInviting} myRole={myRole} />
      <ConfirmDialog
        open={confirmBulk}
        onOpenChange={setConfirmBulk}
        destructive
        title={`Deactivate ${selOff.length === 1 ? '1 person' : `${selOff.length} people`}?`}
        desc='They can’t sign in after their current session ends (at most 24 hours). Their chats, leads and history stay. Inviting someone again turns their account back on with a new password. Give their open chats to someone first: open each person to see them.'
        confirmText='Deactivate'
        isLoading={bulkOff.isPending}
        handleConfirm={() => bulkOff.mutate()}
      />
    </main>
  )
}
