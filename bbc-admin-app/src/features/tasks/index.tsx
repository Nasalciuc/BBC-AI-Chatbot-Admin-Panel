import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { Plus } from 'lucide-react'
import { toast } from 'sonner'
import { type ApiError, createTask, deleteTask, getTasksView, updateTask } from '@/lib/api'
import type { NewTaskBody, TaskKind, TaskPatchBody, TaskRow as Row } from '@/lib/types'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth-store'
import { HeaderActions } from '@/components/header-actions'
import { PageHeader } from '@/components/layout/page-header'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/components/ui/sheet'
import { NewTaskDialog } from './components/new-task-dialog'
import { TaskPanel } from './components/task-panel'
import { TaskRow } from './components/task-row'
import { TaskRules } from './components/task-rules'
import { KIND_FILTERS, countStates, groupTasks, localZone, shownState, summaryLine, zoneCity } from './view'

const MANAGERS = ['owner', 'admin', 'dev']
const TEAM_LEADS = ['supervisor', 'project_manager']

export type TasksSearch = { view?: string; kind?: TaskKind; task?: string; tab?: 'rules' }

function useWide(): boolean {
  const query = '(min-width: 1280px)'
  const [wide, setWide] = useState(() => typeof window !== 'undefined' && window.matchMedia(query).matches)
  useEffect(() => {
    const m = window.matchMedia(query)
    const on = () => setWide(m.matches)
    m.addEventListener('change', on)
    return () => m.removeEventListener('change', on)
  }, [])
  return wide
}

/**
 * Tasks: what each person promised clients in the chats, what clients asked
 * for, and what someone added — design canvas "Tasks · the new feature".
 * Agents see theirs; a supervisor or PM theirs, the team's and Unowned
 * (contacts hidden); owner, admin and dev everyone's, plus Rules.
 */
export function Tasks() {
  const search = useSearch({ strict: false }) as TasksSearch
  const navigate = useNavigate()
  const qc = useQueryClient()
  const me = useAuthStore((s) => s.auth.user)
  const role = me?.role ?? 'sales'
  const isManager = MANAGERS.includes(role)
  const isLead = TEAM_LEADS.includes(role)
  const wide = useWide()
  const tab = search.tab === 'rules' && isManager ? 'rules' : 'tasks'
  const view = search.view ?? (isManager ? 'all' : isLead ? 'team' : 'mine')
  const kind = search.kind ?? null
  const selectedId = search.task ?? null
  const [showDone, setShowDone] = useState(false)
  const [newOpen, setNewOpen] = useState(false)
  const [nowMs, setNowMs] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNowMs(Date.now()), 30_000)
    return () => clearInterval(t)
  }, [])

  const setSearch = (patch: Partial<TasksSearch>) =>
    navigate({ to: '/tasks', search: ((prev: TasksSearch) => ({ ...prev, ...patch })) as never, replace: true })

  const list = useQuery({
    queryKey: ['tasks', view, kind],
    queryFn: () => getTasksView({ view, kind }),
    select: (r) => r.data,
    refetchInterval: 60_000,
    retry: (n, e) => (e as ApiError)?.status !== 503 && n < 2,
    enabled: tab === 'tasks',
  })
  const data = list.data
  // Shown in the viewer's clock, like the chat; the rules' zone only reads promises.
  const tz = localZone()
  const city = zoneCity(tz)
  // Today/Later in the viewer's day, like the times (the API's is the business day).
  const rows = (data?.tasks ?? []).map((r) => ({ ...r, state: shownState(r, nowMs, tz) }))
  const all = [...rows, ...(data?.done ?? [])]
  const selected = all.find((r) => r.id === selectedId) ?? null
  const canAssign = !!data?.can.assign

  const refresh = (id?: string) => {
    qc.invalidateQueries({ queryKey: ['tasks'] })
    qc.invalidateQueries({ queryKey: ['notifications'] })
    qc.invalidateQueries({ queryKey: ['chat-tasks'] })
    if (id) qc.invalidateQueries({ queryKey: ['task', id] })
  }
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: TaskPatchBody; ok?: string }) => updateTask(id, body),
    onSuccess: (_r, v) => {
      refresh(v.id)
      if (v.ok) toast.success(v.ok)
    },
    onError: (e) => toast.error((e as ApiError)?.message ?? 'Couldn’t save the task. Try again.'),
  })
  const create = useMutation({
    mutationFn: (body: NewTaskBody) => createTask(body),
    onSuccess: () => {
      setNewOpen(false)
      refresh()
      toast.success('Task added.')
    },
    onError: (e) => toast.error((e as ApiError)?.message ?? 'Couldn’t add the task. Try again.'),
  })
  const remove = useMutation({
    mutationFn: (id: string) => deleteTask(id),
    onSuccess: () => {
      setSearch({ task: undefined })
      refresh()
      toast.success('Task deleted.')
    },
    onError: (e) => toast.error((e as ApiError)?.message ?? 'Couldn’t delete the task. Try again.'),
  })

  const openChat = (conversationId: string) => navigate({ to: '/chats', search: { highlight: conversationId } as never })
  const rowActions = (r: Row) => ({
    onDone: () => update.mutate({ id: r.id, body: { done: true }, ok: 'Done.' }),
    onSnooze: (at: number, label: string) => update.mutate({ id: r.id, body: { due_at: new Date(at).toISOString(), due_kind: 'picked' }, ok: `Moved: ${label}.` }),
  })

  const subtitle = isManager
    ? `Everyone’s tasks · ${city} time`
    : isLead
      ? `Your team’s tasks · contacts hidden · ${city} time`
      : `${me?.name || 'You'} · what you promised clients, and what they asked for · ${city} time`
  const scopes: { key: string; label: string; n?: number | null }[] = isManager
    ? [{ key: 'all', label: 'All' }, { key: 'mine', label: 'Mine' }, { key: 'unowned', label: 'Unowned', n: data?.unowned_open }]
    : isLead
      ? [{ key: 'mine', label: 'Mine' }, { key: 'team', label: 'Team' }, { key: 'unowned', label: 'Unowned', n: data?.unowned_open }]
      : []
  const notSetUp = list.isError && (list.error as ApiError)?.status === 503
  const groups = groupTasks(rows, view !== 'mine' && canAssign)
  const emptyText =
    view === 'unowned'
      ? 'Nobody’s task is waiting to be given out.'
      : view === 'mine'
        ? 'Nothing to follow up. When you write “I’ll send…” or “I’ll call…” in a chat, it shows up here.'
        : isLead && data && !data.has_team
          ? 'You have no team yet, so only your own tasks and Unowned show here.'
          : 'No open tasks.'

  const panel = selected ? (
    <TaskPanel
      key={selected.id}
      row={selected}
      nowMs={nowMs}
      tz={tz}
      people={data?.people ?? []}
      canAssign={canAssign}
      onClose={() => setSearch({ task: undefined })}
      actions={{
        busy: update.isPending || remove.isPending,
        onDone: (done) => update.mutate({ id: selected.id, body: { done }, ok: done ? 'Done.' : 'Open again.' }),
        onSnooze: (at, label) => update.mutate({ id: selected.id, body: { due_at: new Date(at).toISOString(), due_kind: 'picked' }, ok: `Moved: ${label}.` }),
        onPickDue: (iso) => update.mutate({ id: selected.id, body: { due_at: iso, due_kind: 'picked' }, ok: 'New time saved.' }),
        onAssign: (to) => update.mutate({ id: selected.id, body: to ? { assignee_id: to } : { unowned: true }, ok: to ? 'Given.' : 'Moved to Unowned.' }),
        onNotAPromise: () => update.mutate({ id: selected.id, body: { not_a_promise: true }, ok: 'Removed: not a promise.' }),
        onDelete: () => remove.mutate(selected.id),
        onOpenChat: openChat,
      }}
    />
  ) : null

  return (
    <main id='content' className='flex flex-col gap-4 px-4 pt-6 pb-9 md:px-7 md:pt-7'>
      <PageHeader
        title='Tasks'
        subtitle={subtitle}
        actions={
          <div className='flex items-center gap-2.5'>
            {tab === 'tasks' && !notSetUp && (
              <button type='button' onClick={() => setNewOpen(true)} className='flex h-11 items-center gap-1.5 rounded-full bg-kb-202020 px-5 text-sm font-bold text-on-btn'>
                <Plus aria-hidden='true' className='size-4' />
                New task
              </button>
            )}
            <HeaderActions className='ms-0' />
          </div>
        }
      />

      {isManager && (
        <div role='tablist' aria-label='Tasks' className='flex gap-1 border-b border-k-ececec'>
          {(['tasks', 'rules'] as const).map((t) => (
            <button
              key={t}
              type='button'
              role='tab'
              aria-selected={tab === t}
              onClick={() => setSearch({ tab: t === 'rules' ? 'rules' : undefined, task: undefined })}
              className={cn('-mb-px h-11 border-b-2 px-3.5 text-[15px]', tab === t ? 'border-k-202020 font-bold text-k-202020' : 'border-transparent text-k-555555 hover:text-k-202020')}
            >
              {t === 'rules' ? 'Rules' : 'Tasks'}
            </button>
          ))}
        </div>
      )}

      {tab === 'rules' ? (
        <TaskRules />
      ) : (
        <>
          <div className='flex flex-wrap items-center gap-2.5'>
            {scopes.length > 0 && (
              <div role='tablist' aria-label='Whose tasks' className='flex gap-1 rounded-2xl bg-k-f2f2f2 p-1'>
                {scopes.map((s) => (
                  <button
                    key={s.key}
                    type='button'
                    role='tab'
                    aria-selected={view === s.key}
                    onClick={() => setSearch({ view: s.key, task: undefined })}
                    className={cn('flex h-9 items-center gap-1.5 rounded-xl px-3.5 text-sm', view === s.key ? 'bg-k-ffffff font-bold text-k-202020 shadow-sm' : 'text-k-202020')}
                  >
                    {s.label}
                    {s.n ? <span className='rounded-full bg-k-fff3e0 px-1.5 text-xs font-bold text-k-8a4b00'>{s.n}</span> : null}
                  </button>
                ))}
              </div>
            )}
            <div role='group' aria-label='Kind' className='flex flex-wrap gap-1.5'>
              {KIND_FILTERS.map((f) => (
                <button
                  key={f.label}
                  type='button'
                  aria-pressed={kind === f.key}
                  onClick={() => setSearch({ kind: f.key ?? undefined })}
                  className={cn('h-9 rounded-full px-3.5 text-sm font-bold', kind === f.key ? 'bg-kb-202020 text-on-btn' : 'bg-k-f2f2f2 text-k-202020 hover:bg-k-ececec')}
                >
                  {f.label}
                </button>
              ))}
            </div>
            {data && <p className='m-0 ms-auto text-[13px] text-k-555555'>{summaryLine({ counts: countStates(rows), on_time: data.on_time })}</p>}
          </div>

          {list.isError ? (
            <div role='alert' className='flex flex-wrap items-center gap-3 rounded-2xl bg-k-fff3e0 px-4 py-3 text-sm text-k-8a4b00'>
              <span className='flex-1'>
                <strong>{notSetUp ? 'Tasks aren’t set up yet.' : 'Couldn’t load the tasks.'}</strong>{' '}
                {notSetUp
                  ? 'The database change (migration 051) hasn’t run. Until it does, nothing is caught from the chats.'
                  : 'The list isn’t shown rather than shown empty.'}
              </span>
              {!notSetUp && (
                <button type='button' onClick={() => list.refetch()} className='h-9 rounded-full bg-kb-202020 px-4 text-[13px] font-bold text-on-btn'>
                  Try again
                </button>
              )}
            </div>
          ) : list.isLoading || !data ? (
            <div className='h-64 animate-pulse rounded-[20px] bg-k-f7f7f7' aria-busy='true' />
          ) : (
            <div className='flex items-start gap-4'>
              <div className='flex min-w-0 flex-1 flex-col overflow-hidden rounded-[20px] bg-k-ffffff ring-1 ring-k-ececec'>
                {groups.length === 0 ? (
                  <p className='m-0 px-5 py-10 text-center text-sm text-k-555555'>{emptyText}</p>
                ) : (
                  groups.map((g) => (
                    <section key={g.key} aria-labelledby={`tasks-${g.key}`}>
                      <h2 id={`tasks-${g.key}`} className='m-0 flex flex-wrap items-center gap-2 px-4 pt-4 pb-2 text-[13px] font-bold tracking-[0.04em] uppercase sm:px-[18px]'>
                        <span className={g.key === 'late' ? 'text-k-b3261e' : g.key === 'unowned' ? 'text-k-8a4b00' : 'text-k-202020'}>{g.title}</span>
                        <span className={cn('rounded-full px-1.5 text-xs', g.key === 'late' ? 'bg-k-fdecea text-k-b3261e' : g.key === 'unowned' ? 'bg-k-fff3e0 text-k-8a4b00' : 'bg-k-f2f2f2 text-k-555555')}>
                          {g.rows.length}
                        </span>
                        {g.note && <span className='text-xs font-normal tracking-normal text-k-6b6b6b normal-case'>{g.note}</span>}
                      </h2>
                      <ul className='m-0 list-none p-0'>
                        {g.rows.map((r) => (
                          <TaskRow
                            key={r.id}
                            row={r}
                            nowMs={nowMs}
                            tz={tz}
                            selected={r.id === selectedId}
                            showOwner={view !== 'mine' && canAssign && !(wide && selected)}
                            busy={update.isPending}
                            onOpen={() => setSearch({ task: r.id === selectedId ? undefined : r.id })}
                            onOpenChat={r.conversation_id ? () => openChat(r.conversation_id!) : null}
                            {...rowActions(r)}
                          />
                        ))}
                      </ul>
                    </section>
                  ))
                )}
                <section aria-label='Done this week' className='border-t border-k-ececec'>
                  <div className='flex items-center gap-2 px-4 py-3.5 sm:px-[18px]'>
                    <h2 className='m-0 text-[13px] font-bold tracking-[0.04em] text-k-0a6b35 uppercase'>Done this week</h2>
                    <span className='rounded-full bg-k-e3f6ea px-1.5 text-xs font-bold text-k-0a6b35'>{data.done.length}</span>
                    {data.done.length > 0 && (
                      <button type='button' aria-expanded={showDone} onClick={() => setShowDone((v) => !v)} className='ms-auto text-sm font-bold text-k-5b45e0 hover:underline'>
                        {showDone ? 'Hide' : 'Show'}
                      </button>
                    )}
                  </div>
                  {showDone && (
                    <ul className='m-0 list-none p-0'>
                      {data.done.map((r) => (
                        <li key={r.id} className='flex items-center gap-3 border-t border-k-ececec px-4 py-3 text-sm sm:px-[18px]'>
                          <span aria-hidden='true' className='flex size-[22px] flex-none items-center justify-center rounded-full bg-k-0a6b35 text-xs text-k-ffffff'>✓</span>
                          <button type='button' onClick={() => setSearch({ task: r.id })} className='min-w-0 flex-1 truncate text-start text-k-202020'>
                            {r.title}
                          </button>
                          <span className='flex-none text-xs text-k-6b6b6b'>{r.done_by_name ? `${r.done_by_name} · ` : ''}{r.client || ''}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
              </div>
              {wide && panel && (
                <aside aria-label='Task' className='sticky top-4 w-[440px] flex-none rounded-[20px] bg-k-ffffff p-5 ring-1 ring-k-ececec'>
                  {panel}
                </aside>
              )}
            </div>
          )}
          {!wide && (
            <Sheet open={!!panel} onOpenChange={(o) => !o && setSearch({ task: undefined })}>
              <SheetContent className='w-full overflow-y-auto bg-k-ffffff p-5 sm:max-w-[480px] [&>button]:hidden'>
                <SheetTitle className='sr-only'>Task</SheetTitle>
                <SheetDescription className='sr-only'>The task, the chat it came from and its history</SheetDescription>
                {panel}
              </SheetContent>
            </Sheet>
          )}
        </>
      )}

      {newOpen && data && (
        <NewTaskDialog
          open={newOpen}
          onOpenChange={setNewOpen}
          nowMs={nowMs}
          tz={tz}
          meId={me?.accountNo ?? ''}
          people={data.people ?? []}
          canAssign={canAssign}
          busy={create.isPending}
          onAdd={(body) => create.mutate(body)}
        />
      )}
    </main>
  )
}
