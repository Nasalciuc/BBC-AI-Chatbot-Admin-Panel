import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { Check } from 'lucide-react'
import { toast } from 'sonner'
import { type ApiError, getUserHandover, inviteUser, reassignConversation, updateUser } from '@/lib/api'
import type { AccessEvent, PanelUser } from '@/lib/types'
import { cn } from '@/lib/utils'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { PersonAvatar } from '@/components/person-avatar'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/components/ui/sheet'
import { Switch } from '@/components/ui/switch'
import { dayMonth, firstName } from '@/features/chats/view'
import {
  ROLE_LABEL,
  ROLE_ORDER,
  TUNNEL_LONG,
  accessSentence,
  avatarTone,
  canDo,
  cannotDo,
  isInvite,
  lastActive,
  roleChange,
  statusOf,
  tunnelFor,
} from '../view'
import { TeamCell } from './team-cell'

const h3 = 'm-0 text-[11px] font-bold tracking-[0.05em] text-k-6b6b6b uppercase'
const btn = 'h-9 rounded-full px-4 text-[13px] font-bold focus-visible:outline-2 focus-visible:outline-k-6ea8ff disabled:opacity-50'

export function PersonPanel({
  user,
  me,
  canManage,
  everyone,
  log,
  logFailed,
  logLimit,
  onRetryLog,
  nowMs,
  onClose,
}: {
  user: PanelUser | null
  me: { id: string; role: string }
  /** owner/admin/dev: the backend lets only them change access. */
  canManage: boolean
  everyone: PanelUser[]
  /** The access log (owner/admin/dev only); undefined while it loads. */
  log: AccessEvent[] | undefined
  logFailed: boolean
  /** How many of the latest events the page asked for. */
  logLimit: number
  onRetryLog: () => void
  nowMs: number
  onClose: () => void
}) {
  const qc = useQueryClient()
  const [wide, setWide] = useState(false)
  const [pendingRole, setPendingRole] = useState<string | null>(null)
  const [giveTo, setGiveTo] = useState('')
  const [confirmOff, setConfirmOff] = useState(false)
  // The panel stays mounted between people: each one starts clean, so a role
  // picked (and not saved) for someone else is never offered for them.
  const [shownId, setShownId] = useState<string | null>(user?.id ?? null)
  if ((user?.id ?? null) !== shownId) {
    setShownId(user?.id ?? null)
    setPendingRole(null)
    setGiveTo('')
    setConfirmOff(false)
  }
  const open = !!user
  const st = user ? statusOf(user, nowMs) : null
  const handover = useQuery({
    queryKey: ['user-handover', user?.id],
    queryFn: () => getUserHandover(user!.id),
    enabled: open && canManage && st?.key === 'active' && user?.id !== me.id,
  })
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['panel-users'] })
    qc.invalidateQueries({ queryKey: ['access-log'] })
    qc.invalidateQueries({ queryKey: ['user-handover', user?.id] })
  }
  const save = useMutation({
    mutationFn: (body: Parameters<typeof updateUser>[1]) => updateUser(user!.id, body),
    onSuccess: () => refresh(),
    onError: (e) => toast.error((e as ApiError)?.message ?? 'Couldn’t save. Try again.'),
  })
  const invite = useMutation({
    mutationFn: () =>
      inviteUser({ name: user!.name || user!.email, email: user!.email, role: user!.role, tunnel_scope: user!.tunnel_scope || tunnelFor(user!.role) }),
    onSuccess: (r) => {
      const sent = (r.data as { email_sent?: boolean }).email_sent !== false
      if (sent) toast.success(`Invite sent to ${user!.email}. The link works for 48 hours.`)
      else toast.warning(`The account is ready, but the e-mail didn’t go out. Try Resend invite.`)
      refresh()
    },
    onError: (e) => toast.error((e as ApiError)?.message ?? 'Couldn’t send the invite. Try again.'),
  })
  const give = useMutation({
    mutationFn: async (agentId: string) => {
      const chats = handover.data?.open_chats ?? []
      for (const c of chats) await reassignConversation(c.id, agentId)
      return chats.length
    },
    onSuccess: (n) => {
      toast.success(`${n === 1 ? 'The chat was' : `${n} chats were`} given to ${everyone.find((u) => u.id === giveTo)?.name ?? 'them'}.`)
      setGiveTo('')
      refresh()
    },
    onError: (e) => {
      toast.error((e as ApiError)?.message ?? 'Couldn’t give the chats. Some may have moved; check Conversations.')
      refresh()
    },
  })

  if (!user || !st) return null
  const first = firstName(user.name) || user.email
  const self = user.id === me.id
  const ownerLock = user.role === 'owner' && me.role !== 'owner'
  const owners = everyone.filter((u) => u.role === 'owner' && u.is_active)
  const lastOwner = user.role === 'owner' && owners.length <= 1
  const roleOptions = ROLE_ORDER.filter((r) => r !== 'owner' || me.role === 'owner')
  const diff = pendingRole ? roleChange(user.role, pendingRole) : null
  const history = (log ?? []).filter((e) => e.target_id === user.id)
  const operators = everyone.filter(
    (u) => u.id !== user.id && u.is_active && !!u.last_seen_at && (u.role === user.role) && (u.role === 'sales' || u.role === 'support')
  )
  const canEditAccess = canManage && !self && !ownerLock
  const chatsRole = user.role === 'sales' || user.role === 'support'

  const signIn = (() => {
    if (st.key === 'pending') return { title: 'Invite sent', text: `${user.invite?.sent_at ? `Sent ${dayMonth(user.invite.sent_at.slice(0, 10))}. ` : ''}${st.sub}. The link sets their password.`, bg: 'bg-k-fff3e0' }
    if (st.key === 'expired') return { title: 'The invite expired', text: `${st.sub}. Send a new one; the new link works for 48 hours.`, bg: 'bg-k-fdecea' }
    if (st.key === 'never')
      return user.invite?.used_at
        ? { title: 'Not signed in yet', text: `They set their password with the invite on ${dayMonth(user.invite.used_at.slice(0, 10))}. No sign-in since.`, bg: 'bg-k-f7f7f7' }
        : { title: 'Not signed in yet', text: 'No invite on record. Send one to set their password.', bg: 'bg-k-f7f7f7' }
    if (st.key === 'deactivated') return { title: 'Can’t sign in', text: 'The account is deactivated. Inviting them again turns it back on and sets a new password.', bg: 'bg-k-f7f7f7' }
    if (user.has_password === false)
      return { title: 'Signs in from the CRM', text: `No password is set: they open the panel from the CRM. Last active: ${lastActive(user.last_seen_at, nowMs).toLowerCase()}.`, bg: 'bg-k-f7f7f7' }
    return {
      title: 'Signs in with a password',
      text: `Last active: ${lastActive(user.last_seen_at, nowMs).toLowerCase()}. If the CRM single sign-on is on, they also sign in straight from the CRM.`,
      bg: 'bg-k-f7f7f7',
    }
  })()

  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className={cn('w-full gap-0 overflow-y-auto bg-k-ffffff p-0 sm:max-w-none [&>button]:z-20', wide ? 'sm:w-[720px]' : 'sm:w-[500px]')}>
        <div className='flex items-start gap-3 border-b border-k-ececec px-6 pt-5 pb-4 pe-12'>
          <PersonAvatar name={user.name || user.email} tone={avatarTone(user, st.key)} size={52} />
          <div className='min-w-0 flex-1'>
            <SheetTitle className='m-0 truncate text-[22px] font-bold text-k-202020'>
              {user.name || user.email}
              {self && <span className='ms-1 font-normal text-k-6b6b6b'>(you)</span>}
            </SheetTitle>
            <SheetDescription className='m-0 truncate text-[13px] text-k-555555'>{user.email}</SheetDescription>
            <span className={cn('mt-1 inline-block rounded-full px-2 py-0.5 text-[11px] font-bold', st.chip)}>{st.label}</span>
          </div>
          <button type='button' onClick={() => setWide((w) => !w)} className={cn(btn, 'hidden border border-k-c9c9c9 text-k-202020 sm:block')}>
            {wide ? 'Narrower' : 'Wider'}
          </button>
        </div>

        <div className='flex flex-col gap-6 px-6 py-5'>
          <section aria-label='Profile' className='flex flex-col gap-2'>
            <h3 className={h3}>Profile</h3>
            <dl className='m-0 grid grid-cols-[120px_1fr] gap-x-3 gap-y-1.5 text-sm'>
              <dt className='text-k-555555'>Phone</dt>
              <dd className='m-0 text-k-202020'>{user.phone || '—'}</dd>
              <dt className='text-k-555555'>Team</dt>
              <dd className='m-0 text-k-202020'>
                <TeamCell teamId={user.team_id} />
              </dd>
              <dt className='text-k-555555'>Joined</dt>
              <dd className='m-0 text-k-202020'>{user.created_at ? new Date(user.created_at).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : '—'}</dd>
            </dl>
            <p className='m-0 text-xs text-k-6b6b6b'>People change their own name, phone and photo in Settings → Profile.</p>
          </section>

          <section aria-label='Access' className='flex flex-col gap-2.5'>
            <h3 className={h3}>Access</h3>
            <div className='grid grid-cols-[120px_1fr] items-center gap-x-3 gap-y-2 text-sm'>
              <label htmlFor='person-role' className='text-k-555555'>
                Role
              </label>
              <select
                id='person-role'
                value={pendingRole ?? user.role}
                disabled={!canEditAccess || save.isPending}
                onChange={(e) => setPendingRole(e.target.value === user.role ? null : e.target.value)}
                className='h-10 w-fit rounded-xl border border-k-d9d9d9 bg-k-ffffff px-3 text-sm text-k-202020 disabled:opacity-60'
              >
                {(roleOptions.includes(user.role as (typeof ROLE_ORDER)[number]) ? roleOptions : [user.role, ...roleOptions]).map((r) => (
                  <option key={r} value={r}>
                    {ROLE_LABEL[r] ?? r}
                  </option>
                ))}
              </select>
              <span className='text-k-555555'>Tunnel</span>
              <span className='text-k-202020'>{TUNNEL_LONG[tunnelFor(pendingRole ?? user.role)]}</span>
              {chatsRole && (
                <>
                  <label htmlFor='person-chats' className='text-k-555555'>
                    Receives chats
                  </label>
                  <span className='flex items-center gap-2'>
                    <Switch
                      id='person-chats'
                      checked={user.chat_enabled !== false}
                      disabled={!canEditAccess || save.isPending}
                      onCheckedChange={(v) =>
                        save.mutate(
                          { chat_enabled: v },
                          { onSuccess: () => toast.success(v ? `${first} receives chats again.` : `${first} no longer receives chats.`) }
                        )
                      }
                    />
                    <span className='text-xs text-k-6b6b6b'>Off takes them out of the queue and the routing.</span>
                  </span>
                </>
              )}
            </div>
            {!canManage && <p className='m-0 text-xs text-k-6b6b6b'>Only an owner, an admin or a developer changes access.</p>}
            {canManage && self && <p className='m-0 text-xs text-k-6b6b6b'>Nobody can change their own role.</p>}
            {canManage && ownerLock && <p className='m-0 text-xs text-k-6b6b6b'>Only an owner can change another owner.</p>}
            {diff && pendingRole && (
              <div className='flex flex-col gap-2 rounded-2xl bg-k-f3f0ff p-3.5 text-[13px] text-k-2c2266'>
                <strong>
                  {ROLE_LABEL[user.role]} → {ROLE_LABEL[pendingRole]}
                </strong>
                {diff.gains.length > 0 && <span>Can now: {diff.gains.join(', ').toLowerCase()}.</span>}
                {diff.losses.length > 0 && <span>Can’t anymore: {diff.losses.join(', ').toLowerCase()}.</span>}
                <span>It applies when they next sign in; a session already open keeps the old role for up to 24 hours.</span>
                <div className='flex gap-2'>
                  <button
                    type='button'
                    disabled={save.isPending}
                    onClick={() =>
                      save.mutate(
                        { role: pendingRole, tunnel_scope: tunnelFor(pendingRole) },
                        {
                          onSuccess: () => {
                            toast.success(`${first} is now ${ROLE_LABEL[pendingRole]}. It applies when they next sign in.`)
                            setPendingRole(null)
                          },
                        }
                      )
                    }
                    className={cn(btn, 'bg-kb-202020 text-on-btn')}
                  >
                    Save the new role
                  </button>
                  <button type='button' onClick={() => setPendingRole(null)} className={cn(btn, 'text-k-202020')}>
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </section>

          <section aria-label='What they can do' className='flex flex-col gap-2'>
            <h3 className={h3}>
              What {first} can do as {ROLE_LABEL[user.role] ?? user.role}
            </h3>
            <ul className='m-0 flex list-none flex-col gap-1.5 p-0'>
              {canDo(user.role).map((c) => (
                <li key={c.label} className='flex items-center gap-2.5 text-sm text-k-202020'>
                  <Check aria-hidden='true' className='size-3.5 flex-none text-k-0a6b35' />
                  <span className='flex-1'>{c.label}</span>
                  {c.scope && <span className='text-k-555555'>{c.scope}</span>}
                </li>
              ))}
            </ul>
            {cannotDo(user.role).length > 0 && <p className='m-0 text-xs text-k-6b6b6b'>Can’t: {cannotDo(user.role).join(', ')}.</p>}
          </section>

          <section aria-label='Sign-in' className='flex flex-col gap-2'>
            <h3 className={h3}>Sign-in</h3>
            <div className={cn('rounded-2xl px-3.5 py-3 text-sm', signIn.bg)}>
              <strong className='block text-k-202020'>{signIn.title}</strong>
              <span className='text-[13px] text-k-555555'>{signIn.text}</span>
            </div>
            {canManage && isInvite(st.key) && !ownerLock && (
              <button type='button' disabled={invite.isPending} onClick={() => invite.mutate()} className={cn(btn, 'self-start bg-kb-202020 text-on-btn')}>
                {invite.isPending ? 'Sending…' : st.key === 'pending' ? 'Resend invite' : 'Send a new invite'}
              </button>
            )}
            {st.key === 'active' && user.has_password !== false && (
              <p className='m-0 text-xs text-k-6b6b6b'>
                Forgot their password? There’s no reset link yet: deactivate, then invite them again — the new invite sets a new password.
              </p>
            )}
          </section>

          {canManage && (
            <section aria-label='Changes to their access' className='flex flex-col gap-2'>
              <h3 className={h3}>Changes to {first}’s access</h3>
              {logFailed && log === undefined ? (
                <div role='alert' className='flex flex-wrap items-center gap-3'>
                  <p className='m-0 text-sm text-k-b3261e'>Couldn’t load the changes to {first}’s access.</p>
                  <button type='button' onClick={onRetryLog} className={cn(btn, 'border border-k-c9c9c9 text-k-202020')}>
                    Try again
                  </button>
                </div>
              ) : log === undefined ? (
                <p className='m-0 text-sm text-k-6b6b6b'>Loading…</p>
              ) : history.length === 0 ? (
                <p className='m-0 text-sm text-k-555555'>
                  {log.length >= logLimit ? `None among the latest ${logLimit} changes to anyone’s access.` : 'No changes recorded.'}{' '}
                  Profile edits and team moves aren’t recorded.
                </p>
              ) : (
                <ul className='m-0 flex list-none flex-col p-0'>
                  {history.map((e) => {
                    const s = accessSentence(e)
                    return (
                      <li key={`${e.at}-${e.kind}`} className='flex items-start justify-between gap-3 border-t border-k-ececec py-2.5 text-sm first:border-t-0'>
                        <span className='text-k-202020'>
                          <strong>{s.actor}</strong> {s.text}
                        </span>
                        <span className='flex-none text-xs text-k-6b6b6b'>{dayMonth(e.at.slice(0, 10))}</span>
                      </li>
                    )
                  })}
                </ul>
              )}
            </section>
          )}

          {canManage && !self && !ownerLock && st.key === 'active' && (
            <section aria-label={`Before ${first} goes`} className='flex flex-col gap-2.5 rounded-2xl bg-k-f7f7f7 p-4'>
              <strong className='text-sm text-k-202020'>Before {first} goes</strong>
              {handover.isLoading ? (
                <p className='m-0 text-[13px] text-k-6b6b6b'>Looking at their chats…</p>
              ) : handover.isError ? (
                <p className='m-0 text-[13px] text-k-8a4b00'>Couldn’t check their open chats. Look in Conversations before deactivating.</p>
              ) : handover.data ? (
                <>
                  {handover.data.open_chats.length > 0 ? (
                    <>
                      <p className='m-0 text-[13px] leading-[1.45] text-k-202020'>
                        <strong>{handover.data.open_chats.length === 1 ? '1 chat' : `${handover.data.open_chats.length} chats`}</strong> open now:{' '}
                        {handover.data.open_chats.map((c) => c.first_name || 'a client').join(', ')}. Visitors would be left waiting, so give them
                        to someone first.
                      </p>
                      {operators.length > 0 ? (
                        <div className='flex flex-wrap items-center gap-2'>
                          <select
                            aria-label='Give the open chats to'
                            value={giveTo}
                            onChange={(e) => setGiveTo(e.target.value)}
                            className='h-9 rounded-xl border border-k-d9d9d9 bg-k-ffffff px-3 text-[13px] text-k-202020'
                          >
                            <option value=''>Give them to…</option>
                            {operators.map((o) => (
                              <option key={o.id} value={o.id}>
                                {o.name || o.email}
                              </option>
                            ))}
                          </select>
                          <button
                            type='button'
                            disabled={!giveTo || give.isPending}
                            onClick={() => give.mutate(giveTo)}
                            className={cn(btn, 'bg-kb-202020 text-on-btn')}
                          >
                            {give.isPending ? 'Giving…' : 'Give the chats'}
                          </button>
                        </div>
                      ) : (
                        <p className='m-0 text-[13px] text-k-8a4b00'>
                          No other {ROLE_LABEL[user.role]?.toLowerCase() ?? 'operator'} is active to take them. Give them from{' '}
                          <Link to='/chats' className='font-bold underline'>
                            Conversations
                          </Link>
                          .
                        </p>
                      )}
                    </>
                  ) : (
                    <p className='m-0 text-[13px] text-k-202020'>No chat is open with {first} now.</p>
                  )}
                  <p className='m-0 text-[13px] leading-[1.45] text-k-444444'>
                    {handover.data.clients_90_days === 1 ? '1 client' : `${handover.data.clients_90_days} clients`} talked with {first} in the last{' '}
                    {handover.data.window_days} days. When they come back, the chat goes to the shared queue instead of to {first}.
                  </p>
                  <p className='m-0 text-[13px] text-k-444444'>
                    {handover.data.leads
                      ? `${handover.data.leads === 1 ? '1 lead' : `${handover.data.leads} leads`} from ${first}’s chats stay as they are, in Leads and in the CRM.`
                      : 'No leads to look after.'}
                  </p>
                </>
              ) : null}
            </section>
          )}

          {canManage && !self && !ownerLock && (
            <div className='flex flex-col gap-2 pb-4'>
              {lastOwner && st.key === 'active' ? (
                <p className='m-0 text-[13px] text-k-8a4b00'>{first} is the only owner. Make someone else an owner first.</p>
              ) : st.key === 'deactivated' ? (
                <button type='button' disabled={invite.isPending} onClick={() => invite.mutate()} className={cn(btn, 'self-start border border-kl-202020 text-k-202020')}>
                  {invite.isPending ? 'Sending…' : 'Turn the account back on and send an invite'}
                </button>
              ) : (
                <button type='button' onClick={() => setConfirmOff(true)} className={cn(btn, 'self-start border border-k-b3261e text-k-b3261e')}>
                  {isInvite(st.key) ? 'Cancel the invite…' : 'Deactivate…'}
                </button>
              )}
            </div>
          )}
        </div>

        <ConfirmDialog
          open={confirmOff}
          onOpenChange={setConfirmOff}
          destructive
          title={isInvite(st.key) ? `Cancel ${first}’s invite?` : `Deactivate ${first}?`}
          desc={
            isInvite(st.key)
              ? 'The account is turned off and the invite link stops mattering. Inviting them again turns it back on.'
              : `${first} can’t sign in after their current session ends (at most 24 hours). Their chats, leads and history stay. Inviting them again turns the account back on with a new password.`
          }
          confirmText={isInvite(st.key) ? 'Cancel the invite' : 'Deactivate'}
          isLoading={save.isPending}
          handleConfirm={() =>
            save.mutate(
              { is_active: false },
              {
                onSuccess: () => {
                  toast.success(isInvite(st.key) ? `${first}’s invite is cancelled.` : `${first} is deactivated.`)
                  setConfirmOff(false)
                },
              }
            )
          }
        />
      </SheetContent>
    </Sheet>
  )
}
