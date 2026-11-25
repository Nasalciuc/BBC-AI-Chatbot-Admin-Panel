import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Laptop, Lock, Smartphone } from 'lucide-react'
import { toast } from 'sonner'
import { claimVisitor, getLiveVisitors, releaseVisitor, sendHello } from '@/lib/api'
import type { LiveVisitor } from '@/lib/types'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth-store'
import { useLiveStore } from '@/stores/live-store'
import { HeaderActions } from '@/components/header-actions'
import { PageHeader } from '@/components/layout/page-header'
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import {
  ALEX_GAVE_UP_S, GROUP_LABEL, HELLO_MAX, HELLO_RESTRICTED, ROW_LABEL, activityText, alexCardLine, alexLine, alexWaiting,
  claimProblem, clockS, groupHello, groupOf, heldBy, helloDraft, helloProblem, listed, onSite, onSiteSeconds, ordered, pageLine,
  rowState, sourceLabel, testLine, tripLine, type HelloGroup,
} from './view'

const STATE_CLS: Record<string, string> = {
  free: 'bg-k-f2f2f2 text-k-555555',
  mine: 'bg-k-e3f6ea text-k-0a6b35',
  taken: 'bg-k-fff3e0 text-k-8a4b00',
  declined: 'bg-k-f2f2f2 text-k-8a8a8a',
  in_chat: 'bg-k-efeaff text-k-4a36c4',
}

const btn = 'h-9 rounded-xl px-3.5 text-sm font-semibold disabled:opacity-50'
const quiet = 'bg-k-ffffff text-k-202020 shadow-[inset_0_0_0_1px_var(--k-e3e2e2)]'

const SENT = 'Sent. On a computer their chat opens with your words; on a phone they show beside the chat button. Their answer opens the chat in your list, already yours.'

/** "On the site now": the visitors Alex's card didn't start a chat with, and a hello from a person (contracts/live.md). */
export function LiveVisitors() {
  const pass = useLiveStore((s) => s.pass)
  const me = useAuthStore((s) => s.auth.user)
  const meId = me?.accountNo
  const qc = useQueryClient()
  const [writing, setWriting] = useState<LiveVisitor | null>(null)
  const [text, setText] = useState('')
  // The group the editor's draft was written for, sent with it (helloDraft).
  const [writingGroup, setWritingGroup] = useState<HelloGroup | null>(null)
  // The agent-first test: Alex's countdown on a row ticks each second while he waits for anyone listed.
  const [tickS, setTickS] = useState(() => Math.floor(Date.now() / 1000))

  const list = useQuery({
    queryKey: ['live-visitors'],
    queryFn: () => getLiveVisitors(pass!),
    enabled: !!pass,
    refetchInterval: 5_000,
  })
  // Between ticks (none while nobody is waited on) the list's own answers keep the clock fresh.
  const nowS = clockS(tickS, list.dataUpdatedAt)
  const rows = ordered(listed(list.data?.data ?? [], meId, nowS), meId, nowS)
  const anyWaiting = rows.some((v) => alexWaiting(v, nowS))
  useEffect(() => {
    if (!anyWaiting) return
    const t = setInterval(() => setTickS(Math.floor(Date.now() / 1000)), 1000)
    return () => clearInterval(t)
  }, [anyWaiting])
  const test = testLine(pass?.test)
  const held = heldBy(rows, meId)
  const maxClaims = list.data?.max_claims ?? 3
  const firstName = (me?.name || '').split(/\s+/)[0] || ''
  // The hello a row starts from: its group's, ready to send (the owner, 25 Nov 2026), or one built from the page.
  const nowMs = nowS * 1000
  const openEditor = (v: LiveVisitor) => {
    const draft = helloDraft(v, nowMs, firstName)
    setText(draft.words)
    setWritingGroup(draft.group)
    setWriting(v)
  }

  const claim = useMutation({
    mutationFn: (v: LiveVisitor) => claimVisitor(pass!, v.visitor_id),
    onSuccess: (res, v) => {
      const problem = claimProblem(res.state)
      qc.invalidateQueries({ queryKey: ['live-visitors'] })
      if (problem) {
        toast.error(problem)
        return
      }
      openEditor(v)
    },
    onError: (e: Error) => toast.error(`Couldn’t take this visitor: ${e.message}`),
  })
  // One tap (the owner, 25 Nov 2026): a visitor nobody holds is taken, then gets the group's hello as it is.
  const quick = useMutation({
    mutationFn: async ({ v, words, group }: { v: LiveVisitor; words: string; group: HelloGroup }) => {
      if (rowState(v, meId) !== 'mine') {
        const problem = claimProblem((await claimVisitor(pass!, v.visitor_id)).state)
        if (problem) throw new Error(problem)
      }
      return sendHello(v.visitor_id, words, group)
    },
    onSuccess: () => {
      toast.success(SENT)
      qc.invalidateQueries({ queryKey: ['live-visitors'] })
    },
    onError: (e: Error) => {
      toast.error(e.message)
      qc.invalidateQueries({ queryKey: ['live-visitors'] })
    },
  })
  const release = useMutation({
    mutationFn: (v: LiveVisitor) => releaseVisitor(pass!, v.visitor_id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['live-visitors'] }),
    onError: (e: Error) => toast.error(`Couldn’t let this visitor go: ${e.message}`),
  })
  const send = useMutation({
    // With the group the editor's draft was written for: the answers are counted per group.
    mutationFn: ({ v, words, group }: { v: LiveVisitor; words: string; group: HelloGroup | null }) =>
      sendHello(v.visitor_id, words, group),
    onSuccess: (_res, { v }) => {
      toast.success(SENT)
      // Close the editor this hello came from, never another: the agent may have opened another visitor's
      // while it went (CodeRabbit #273).
      setWriting((cur) => (cur?.visitor_id === v.visitor_id ? null : cur))
      qc.invalidateQueries({ queryKey: ['live-visitors'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })
  const problem = writing ? helloProblem(text) : null

  return (
    <main id='content' className='flex flex-col gap-[18px] px-4 pt-6 pb-9 md:px-7 md:pt-7'>
      <PageHeader
        title='On the site now'
        subtitle={`Visitors Skye’s card didn’t start a chat with: no answer ${ALEX_GAVE_UP_S} s after it showed, or “Not now”. Say hello: on a computer their chat opens with your words, on a phone they show beside the chat button. Their answer opens the chat, already yours.`}
        actions={<HeaderActions className='ms-0' />}
      />
      {!pass ? (
        <p className='text-sm text-k-6b6b6b'>On the site now isn’t switched on for you. It appears here once it is.</p>
      ) : list.isLoading ? (
        <p className='text-sm text-k-6b6b6b'>Loading…</p>
      ) : list.isError ? (
        <p className='text-sm text-k-6b6b6b'>
          Couldn’t load who is on the site: {list.error instanceof Error ? list.error.message : 'unknown error'}. Trying again.
        </p>
      ) : rows.length === 0 ? (
        <p className='text-sm text-k-6b6b6b'>
          Nobody to greet right now. A visitor appears here {ALEX_GAVE_UP_S} s after Skye’s card goes unanswered, or when they say
          “Not now” to it, while they are on the site and have no chat.
        </p>
      ) : (
        <>
          {test && <p className='rounded-[14px] bg-k-fff3e0 px-3.5 py-2.5 text-sm text-k-8a4b00'>{test}</p>}
          {pass.can_say_hello && (
            <p className='text-sm text-k-555555'>
              You hold {held} of {maxClaims}. A visitor you hold is yours for 10 minutes; one hello each.
            </p>
          )}
          <ul className='flex flex-col gap-2.5' aria-label='Visitors on the site'>
            {rows.map((v) => {
              const state = rowState(v, meId)
              const trip = tripLine(v)
              const Device = v.device === 'phone' ? Smartphone : Laptop
              const group = groupOf(v, nowMs)
              const ready = group ? groupHello(group, v, firstName) : ''
              const busy = claim.isPending || quick.isPending
              return (
                <li key={v.visitor_id} className='flex flex-col gap-2 rounded-[20px] bg-k-f7f7f7 p-4 md:flex-row md:items-center md:gap-4'>
                  <div className='flex min-w-0 flex-1 flex-col gap-1'>
                    <div className='flex flex-wrap items-center gap-2 text-sm'>
                      <b className='text-k-202020'>{onSite(onSiteSeconds(v))} on the site</b>
                      <span className='rounded-full bg-k-ffffff px-2 py-0.5 text-xs text-k-555555'>{sourceLabel(v.source)}</span>
                      <Device aria-label={v.device === 'phone' ? 'On a phone' : 'On a computer'} className='size-4 text-k-6b6b6b' />
                      <span className={cn('rounded-full px-2 py-0.5 text-xs font-medium', STATE_CLS[state])}>{ROW_LABEL[state]}</span>
                      {group && (
                        <span className='rounded-full bg-k-efeaff px-2 py-0.5 text-xs font-medium text-k-4a36c4'>{GROUP_LABEL[group]}</span>
                      )}
                      {alexCardLine(v) && (
                        <span className='rounded-full bg-k-ffffff px-2 py-0.5 text-xs text-k-555555'>{alexCardLine(v)}</span>
                      )}
                      {alexLine(v, nowS) && (
                        <span className='rounded-full bg-k-fff3e0 px-2 py-0.5 text-xs font-medium tabular-nums text-k-8a4b00'>
                          Test · {alexLine(v, nowS)}
                        </span>
                      )}
                    </div>
                    <span className='truncate text-sm text-k-202020'>{trip || 'No trip on the page'}</span>
                    {activityText(v) && (
                      <span className='text-xs leading-[1.45] text-k-4a36c4'>{activityText(v)}</span>
                    )}
                    <span className='truncate text-xs text-k-6b6b6b'>{pageLine(v)}</span>
                    {ready && (state === 'free' || state === 'mine') && (
                      <span className='line-clamp-2 text-xs leading-[1.45] text-k-555555'>Ready: “{ready}”</span>
                    )}
                  </div>
                  <div className='flex flex-none gap-2'>
                    {state === 'free' && (pass.can_say_hello ? (
                      group ? (
                        <>
                          <button type='button' className={cn(btn, 'bg-kb-202020 text-on-btn')}
                            disabled={busy || held >= maxClaims} onClick={() => quick.mutate({ v, words: ready, group })}>
                            Send this hello
                          </button>
                          <button type='button' className={cn(btn, quiet)}
                            disabled={busy || held >= maxClaims} onClick={() => claim.mutate(v)}>
                            Write
                          </button>
                        </>
                      ) : (
                        <button type='button' className={cn(btn, 'bg-kb-202020 text-on-btn')}
                          disabled={busy || held >= maxClaims} onClick={() => claim.mutate(v)}>
                          Say hello
                        </button>
                      )
                    ) : (
                      // Restricted: shown, disabled, with a lock and the reason (CodeRabbit #273).
                      <button type='button' disabled title={HELLO_RESTRICTED}
                        aria-label={`Say hello (${HELLO_RESTRICTED.toLowerCase()})`}
                        className={cn(btn, 'inline-flex cursor-not-allowed items-center gap-1.5 bg-k-f2f2f2 text-k-555555')}>
                        <Lock aria-hidden='true' className='size-3.5' />
                        Say hello
                      </button>
                    ))}
                    {pass.can_say_hello && state === 'mine' && (
                      <>
                        {group && (
                          <button type='button' className={cn(btn, 'bg-kb-202020 text-on-btn')}
                            disabled={quick.isPending} onClick={() => quick.mutate({ v, words: ready, group })}>
                            Send this hello
                          </button>
                        )}
                        <button type='button' className={cn(btn, group ? quiet : 'bg-kb-202020 text-on-btn')}
                          onClick={() => openEditor(v)}>
                          Write
                        </button>
                        <button type='button' className={cn(btn, quiet)}
                          disabled={release.isPending} onClick={() => release.mutate(v)}>
                          Let go
                        </button>
                      </>
                    )}
                  </div>
                </li>
              )
            })}
          </ul>
        </>
      )}
      <Dialog open={!!writing} onOpenChange={(open) => { if (!open) setWriting(null) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Your hello</DialogTitle>
            <DialogDescription>
              {writing ? [tripLine(writing) || pageLine(writing), activityText(writing, 2)].filter(Boolean).join(' · ') : ''}. The
              visitor sees your first name and these words; no phone, e-mail or link: the chat carries them.
            </DialogDescription>
          </DialogHeader>
          <label className='flex flex-col gap-1 text-xs text-k-555555'>
            Message
            <textarea value={text} onChange={(e) => setText(e.target.value)} rows={4} maxLength={HELLO_MAX + 50}
              className='rounded-xl border border-k-e3e2e2 bg-k-ffffff p-3 text-sm text-k-202020' />
          </label>
          <p className={cn('text-xs', problem ? 'text-k-b3261e' : 'text-k-6b6b6b')}>
            {problem || `${text.trim().length} / ${HELLO_MAX}`}
          </p>
          <DialogFooter>
            <button type='button' className={cn(btn, 'bg-kb-202020 text-on-btn')}
              disabled={!!problem || send.isPending || !writing}
              onClick={() => writing && send.mutate({ v: writing, words: text.trim(), group: writingGroup })}>
              Send
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </main>
  )
}
