import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Laptop, Smartphone } from 'lucide-react'
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
  HELLO_MAX, ROW_LABEL, claimProblem, heldBy, helloProblem, onSite, ordered, rowState, sourceLabel,
  suggestedHello, tripLine,
} from './view'

const STATE_CLS: Record<string, string> = {
  free: 'bg-k-f2f2f2 text-k-555555',
  mine: 'bg-k-e3f6ea text-k-0a6b35',
  taken: 'bg-k-fff3e0 text-k-8a4b00',
  declined: 'bg-k-f2f2f2 text-k-8a8a8a',
  in_chat: 'bg-k-efeaff text-k-4a36c4',
}

const btn = 'h-9 rounded-xl px-3.5 text-sm font-semibold disabled:opacity-50'

/** "On the site now": who is on the site, and a hello before they write (contracts/live.md). */
export function LiveVisitors() {
  const pass = useLiveStore((s) => s.pass)
  const me = useAuthStore((s) => s.auth.user)
  const meId = me?.accountNo
  const qc = useQueryClient()
  const [writing, setWriting] = useState<LiveVisitor | null>(null)
  const [text, setText] = useState('')

  const list = useQuery({
    queryKey: ['live-visitors'],
    queryFn: () => getLiveVisitors(pass!),
    enabled: !!pass,
    refetchInterval: 5_000,
  })
  const rows = ordered(list.data?.data ?? [], meId)
  const held = heldBy(rows, meId)
  const maxClaims = list.data?.max_claims ?? 3
  const firstName = (me?.name || '').split(/\s+/)[0] || ''

  const claim = useMutation({
    mutationFn: (v: LiveVisitor) => claimVisitor(pass!, v.visitor_id),
    onSuccess: (res, v) => {
      const problem = claimProblem(res.state)
      qc.invalidateQueries({ queryKey: ['live-visitors'] })
      if (problem) {
        toast.error(problem)
        return
      }
      setText(suggestedHello(v, firstName))
      setWriting(v)
    },
    onError: (e: Error) => toast.error(`Couldn’t take this visitor: ${e.message}`),
  })
  const release = useMutation({
    mutationFn: (v: LiveVisitor) => releaseVisitor(pass!, v.visitor_id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['live-visitors'] }),
    onError: (e: Error) => toast.error(`Couldn’t let this visitor go: ${e.message}`),
  })
  const send = useMutation({
    mutationFn: ({ v, words }: { v: LiveVisitor; words: string }) => sendHello(v.visitor_id, words),
    onSuccess: () => {
      toast.success('Sent. If they tap Reply, the chat opens in your list, already yours.')
      setWriting(null)
      qc.invalidateQueries({ queryKey: ['live-visitors'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })
  const problem = writing ? helloProblem(text) : null

  return (
    <main id='content' className='flex flex-col gap-[18px] px-4 pt-6 pb-9 md:px-7 md:pt-7'>
      <PageHeader
        title='On the site now'
        subtitle='Visitors on the site this minute, without a chat: the page they read and the trip it names. Hold one and say hello first; a tap on Reply opens the chat, already yours.'
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
        <p className='text-sm text-k-6b6b6b'>Nobody on the site right now without a chat.</p>
      ) : (
        <>
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
              return (
                <li key={v.visitor_id} className='flex flex-col gap-2 rounded-[20px] bg-k-f7f7f7 p-4 md:flex-row md:items-center md:gap-4'>
                  <div className='flex min-w-0 flex-1 flex-col gap-1'>
                    <div className='flex flex-wrap items-center gap-2 text-sm'>
                      <b className='text-k-202020'>{onSite(v.seconds_on_site)} on the site</b>
                      <span className='rounded-full bg-k-ffffff px-2 py-0.5 text-xs text-k-555555'>{sourceLabel(v.source)}</span>
                      <Device aria-label={v.device === 'phone' ? 'On a phone' : 'On a computer'} className='size-4 text-k-6b6b6b' />
                      <span className={cn('rounded-full px-2 py-0.5 text-xs font-medium', STATE_CLS[state])}>{ROW_LABEL[state]}</span>
                    </div>
                    <span className='truncate text-sm text-k-202020'>{trip || 'No trip on the page'}</span>
                    <span className='truncate text-xs text-k-6b6b6b'>{v.page}</span>
                  </div>
                  {pass.can_say_hello && (
                    <div className='flex flex-none gap-2'>
                      {state === 'free' && (
                        <button type='button' className={cn(btn, 'bg-kb-202020 text-on-btn')}
                          disabled={claim.isPending || held >= maxClaims} onClick={() => claim.mutate(v)}>
                          Say hello
                        </button>
                      )}
                      {state === 'mine' && (
                        <>
                          <button type='button' className={cn(btn, 'bg-kb-202020 text-on-btn')}
                            onClick={() => { setText(suggestedHello(v, firstName)); setWriting(v) }}>
                            Write
                          </button>
                          <button type='button' className={cn(btn, 'bg-k-ffffff text-k-202020 shadow-[inset_0_0_0_1px_var(--k-e3e2e2)]')}
                            disabled={release.isPending} onClick={() => release.mutate(v)}>
                            Let go
                          </button>
                        </>
                      )}
                    </div>
                  )}
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
              {writing ? tripLine(writing) || writing.page : ''}. The visitor sees your first name and these words; no
              phone, e-mail or link: the chat carries them.
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
              onClick={() => writing && send.mutate({ v: writing, words: text.trim() })}>
              Send
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </main>
  )
}
