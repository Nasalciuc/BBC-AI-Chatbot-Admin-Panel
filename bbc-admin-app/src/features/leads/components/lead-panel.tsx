import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { Check, ChevronLeft, ChevronRight, MessageSquare } from 'lucide-react'
import { toast } from 'sonner'
import { type ApiError, getConversation, getEarlierLeads, getLeadFull, isLockedPushRefusal, pushLeadToCrm, reviewLead, setLeadCrmId } from '@/lib/api'
import type { LeadRow, Lead } from '@/lib/types'
import { cn } from '@/lib/utils'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/components/ui/sheet'
import {
  aboutOf,
  clock,
  dayMonth,
  firstName,
  initials,
  signalsOf,
  threadLines,
  tripRows,
  whereRows,
} from '@/features/chats/view'
import { LEVELS, TONE_DOT, TONE_TEXT, agoText, chatWithText, checklist, crmLine, historyOf, missingText, reviewerText } from '../view'

const h3 = 'm-0 text-[11px] font-bold tracking-[0.05em] text-k-6b6b6b uppercase'
const btn = 'h-9 rounded-full px-4 text-[13px] font-bold focus-visible:outline-2 focus-visible:outline-k-6ea8ff disabled:opacity-60'

function Rows({ rows }: { rows: { label: string; value: string; warn?: boolean }[] }) {
  return (
    <dl className='m-0 grid grid-cols-[120px_1fr] gap-x-3 gap-y-1.5 text-sm'>
      {rows.map((r) => (
        <div key={r.label} className='contents'>
          <dt className='text-k-555555'>{r.label}</dt>
          <dd className={cn('m-0 min-w-0 break-words text-k-202020', r.warn && 'font-bold text-k-8a4b00')}>{r.value}</dd>
        </div>
      ))}
    </dl>
  )
}

function fmtWhen(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const today = new Date()
  const same = d.toDateString() === today.toDateString()
  return same ? `Today, ${clock(iso)}` : `${dayMonth(iso.slice(0, 10))}, ${clock(iso)}`
}

export function LeadPanel({
  row,
  onClose,
  onPatched,
  canReview,
  canSend,
  canLookUp,
  userNames,
  nowMs,
  meId,
  nav,
  reviewFirst = false,
}: {
  row: LeadRow | null
  onClose: () => void
  /** What changed on that lead, so the open panel shows it even if the row
   *  left the list. A lead that is no longer open is left alone. */
  onPatched: (leadId: string, patch: Partial<LeadRow>) => void
  canReview: boolean
  canSend: boolean
  canLookUp: boolean
  userNames: Record<string, string>
  nowMs: number
  meId?: string
  /** "11 of 12 · To review" and the arrows: QA goes from one lead to the next. */
  nav?: { text: string; prev?: () => void; next?: () => void }
  /** QA: the review comes first, and under the conversation when reading it. */
  reviewFirst?: boolean
}) {
  const qc = useQueryClient()
  const [wide, setWide] = useState(false)
  const [reading, setReading] = useState(false)
  const [manual, setManual] = useState('')
  const [notes, setNotes] = useState(row?.qa_notes ?? '')
  const [shownId, setShownId] = useState(row?.id)
  const open = !!row
  const id = row?.id ?? ''
  // Another lead: its own notes, an empty CRM number (one typed for the last
  // lead must never be saved on this one), and back to the lead (not its chat).
  if (row?.id !== shownId) {
    setShownId(row?.id)
    setNotes(row?.qa_notes ?? '')
    setManual('')
    setReading(false)
  }

  const full = useQuery({ queryKey: ['lead', id], queryFn: () => getLeadFull(id), enabled: open })
  const conv = useQuery({
    queryKey: ['lead-conversation', row?.conversation_id],
    queryFn: () => getConversation(row!.conversation_id),
    enabled: open,
  })
  const earlier = useQuery({ queryKey: ['lead-earlier', id], queryFn: () => getEarlierLeads(id), enabled: open && canLookUp })

  const refresh = (leadId: string) => {
    qc.invalidateQueries({ queryKey: ['leads-view'] })
    qc.invalidateQueries({ queryKey: ['lead', leadId] })
  }
  // Each send, save and review names its lead: if you open another lead before
  // the answer lands, it still goes to the lead it was for, never the open one.
  // A conversation locked for abuse (v2 PR-15) answers 409 until the
  // operator says yes: confirmLocked is only ever that yes, never a click.
  const push = useMutation({
    mutationFn: (v: { leadId: string; confirmLocked?: boolean }) => pushLeadToCrm(v.leadId, v.confirmLocked ?? false),
    onSuccess: (r, { leadId }) => {
      toast.success(`Sent to the CRM${r.data?.crm_lead_id ? ` · lead ${r.data.crm_lead_id}` : ''}.`)
      onPatched(leadId, { crm_state: 'in_crm', created_in_crm: true, crm_lead_id: r.data?.crm_lead_id ?? null, created_in_crm_at: new Date().toISOString() })
      refresh(leadId)
    },
    onError: (e, { leadId }): void => {
      if (isLockedPushRefusal(e)) {
        toast.warning('This conversation was locked for abuse.', {
          duration: 10_000,
          action: { label: 'Push anyway', onClick: () => push.mutate({ leadId, confirmLocked: true }) },
        })
        return
      }
      toast.error((e as ApiError)?.message ?? 'The CRM didn’t take it. Try again.')
      refresh(leadId)
    },
  })
  const saveManual = useMutation({
    mutationFn: (v: { leadId: string; crmId: string }) => setLeadCrmId(v.leadId, v.crmId),
    onSuccess: (_r, v) => {
      toast.success(`Saved: CRM lead ${v.crmId}.`)
      if (v.leadId === id) setManual('')
      onPatched(v.leadId, { crm_state: 'in_crm', created_in_crm: true, crm_lead_id: v.crmId, created_in_crm_at: new Date().toISOString() })
      refresh(v.leadId)
    },
    onError: (e) => toast.error((e as ApiError)?.message ?? 'Couldn’t save the lead number. Try again.'),
  })
  const review = useMutation({
    mutationFn: (v: { leadId: string; reviewed: boolean; notes?: string; next?: boolean }) => reviewLead(v.leadId, v.reviewed, v.notes),
    onSuccess: (_r, v) => {
      toast.success(!v.reviewed ? 'Review cleared.' : v.next ? 'Reviewed. Here’s the next one.' : 'Reviewed.')
      const patch: Partial<LeadRow> = {
        reviewed_by_qa: v.reviewed,
        reviewed_at: v.reviewed ? new Date().toISOString() : null,
        reviewed_by: v.reviewed ? (meId ?? null) : null,
      }
      if (v.notes !== undefined) patch.qa_notes = v.notes
      refresh(v.leadId)
      // "…and next" only from the lead still on screen.
      if (v.next && nav?.next && v.leadId === id) nav.next()
      else onPatched(v.leadId, patch)
    },
    onError: (e) => toast.error((e as ApiError)?.message ?? 'Couldn’t save the review. Try again.'),
  })

  if (!row) return null
  const level = LEVELS[row.level]
  const lead = full.data as Lead | undefined
  const signals = signalsOf(lead)
  const about = aboutOf(signals)
  const trip = tripRows((lead ?? row) as Lead, signals)
  const where = whereRows(conv.data?.metadata as Record<string, unknown> | undefined, conv.data?.visitor_phone_country)
  const first = firstName(row.visitor_name)
  const crm = crmLine(row)
  const chatWith = chatWithText(row)
  const messages = conv.data?.messages ?? []
  const reviewer = row.reviewed_by ? (row.reviewed_by === meId ? 'you' : userNames[row.reviewed_by]) : null
  const notesChanged = notes.trim() !== (row.qa_notes ?? '').trim()
  const sending = push.isPending
  const canPushNow = canSend && row.crm_state !== 'in_crm' && row.crm_state !== 'no_phone'

  const reviewBlock = (
  <section
    aria-label='QA review'
    className={cn('flex flex-col gap-2', canReview && !row.reviewed_by_qa && 'rounded-[18px] p-4 ring-2 ring-kl-202020')}
  >
    <h3 className={cn(h3, 'flex items-center justify-between gap-2')}>
      <span>QA review</span>
      <span className='font-normal tracking-normal normal-case'>{reviewerText(row, meId, userNames)}</span>
    </h3>
    {canReview ? (
      <>
        <label className='flex flex-col gap-1.5 text-sm font-bold text-k-202020'>
          Notes
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={4}
            maxLength={2000}
            placeholder='What went well, what to fix'
            className='rounded-xl border border-k-d9d9d9 bg-k-ffffff px-3 py-2.5 text-sm font-normal text-k-202020 placeholder:text-k-8a8a8a focus-visible:outline-2 focus-visible:outline-k-6ea8ff'
          />
        </label>
        <div className='flex flex-wrap items-center gap-2'>
          {!row.reviewed_by_qa ? (
            <button
              type='button'
              disabled={review.isPending}
              onClick={() => review.mutate({ leadId: id, reviewed: true, notes: notes.trim(), next: !!nav?.next })}
              className={cn(btn, 'h-11 bg-kb-202020 px-5 text-sm text-on-btn')}
            >
              {nav?.next ? 'Mark reviewed and next' : 'Mark reviewed'}
            </button>
          ) : (
            <>
              <button
                type='button'
                disabled={review.isPending || !notesChanged}
                onClick={() => review.mutate({ leadId: id, reviewed: true, notes: notes.trim() })}
                className={cn(btn, 'bg-kb-202020 text-on-btn')}
              >
                Save the notes
              </button>
              <button
                type='button'
                disabled={review.isPending}
                onClick={() => review.mutate({ leadId: id, reviewed: false })}
                className={cn(btn, 'border border-kl-202020 text-k-202020')}
              >
                Clear the review
              </button>
            </>
          )}
        </div>
        <p className='m-0 text-xs text-k-6b6b6b'>
          Saved on the lead with your name and the time{row.reviewed_by_qa ? '; saving the notes again makes the review yours' : ''}.
        </p>
      </>
    ) : (
      row.qa_notes && <p className='m-0 text-sm whitespace-pre-wrap text-k-202020'>{row.qa_notes}</p>
    )}
  </section>
  )

  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()}>
      <SheetContent
        className={cn('w-full gap-0 overflow-y-auto bg-k-ffffff p-0 sm:max-w-none [&>button]:z-20', wide ? 'sm:w-[760px]' : 'sm:w-[500px]')}
      >
        <div className='sticky top-0 z-10 flex flex-col gap-3 border-b border-k-ececec bg-k-ffffff px-6 pt-5 pb-4'>
          {nav && (
            <div className='flex items-center justify-end gap-2 pe-8 text-[13px] text-k-555555'>
              <span>{nav.text}</span>
              <button
                type='button'
                aria-label='Previous lead'
                disabled={!nav.prev}
                onClick={nav.prev}
                className='flex size-9 items-center justify-center rounded-full border border-k-d9d9d9 text-k-202020 disabled:opacity-40'
              >
                <ChevronLeft className='size-4' />
              </button>
              <button
                type='button'
                aria-label='Next lead'
                disabled={!nav.next}
                onClick={nav.next}
                className='flex size-9 items-center justify-center rounded-full border border-k-d9d9d9 text-k-202020 disabled:opacity-40'
              >
                <ChevronRight className='size-4' />
              </button>
            </div>
          )}
          <div className='flex items-start gap-3 pe-8'>
            <span aria-hidden='true' className='flex size-[52px] flex-none items-center justify-center rounded-full bg-k-e6f0fb text-lg font-bold text-k-1f5fb0'>
              {initials(row.visitor_name)}
            </span>
            <div className='min-w-0 flex-1'>
              <SheetTitle className='m-0 truncate text-[22px] font-bold text-k-202020'>{row.visitor_name || 'No name'}</SheetTitle>
              <SheetDescription className='mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-k-555555'>
                <span className={cn('rounded-full px-2 py-0.5 text-[11px] font-bold', level.chipCls)}>{level.name}</span>
                {row.chat_number && <span>chat #{row.chat_number}</span>}
                <span>· {chatWith.ai ? 'only the AI' : `chat with ${chatWith.text}`}</span>
                <span>· {agoText(row.created_at, nowMs)}</span>
              </SheetDescription>
            </div>
            <button
              type='button'
              onClick={() => setWide((w) => !w)}
              className='hidden h-9 flex-none rounded-full border border-k-c9c9c9 px-3.5 text-[13px] font-bold text-k-202020 sm:block'
            >
              {wide ? 'Narrower' : 'Wider'}
            </button>
          </div>
          <button
            type='button'
            onClick={() => setReading((v) => !v)}
            aria-expanded={reading}
            className='flex h-11 items-center justify-center gap-2 rounded-full bg-kb-202020 text-[15px] font-bold text-on-btn focus-visible:outline-2 focus-visible:outline-k-6ea8ff'
          >
            <MessageSquare className='size-4' />
            {reading ? 'Back to the lead' : `Read the conversation${messages.length ? ` · ${messages.length} messages` : ''}`}
          </button>
        </div>

        {reading ? (
          <section aria-label='Conversation' className='flex flex-col gap-2 px-6 py-5'>
            {conv.isLoading ? (
              <p className='text-sm text-k-6b6b6b'>Loading the conversation…</p>
            ) : conv.isError ? (
              <p role='alert' className='text-sm text-k-8a4b00'>Couldn’t load the conversation.</p>
            ) : messages.length === 0 ? (
              <p className='text-sm text-k-6b6b6b'>No messages in this chat.</p>
            ) : (
              threadLines(messages).map((l) => (
                <div key={l.id} className='flex flex-col'>
                  {l.showDay && <span className='my-2 text-center text-xs text-k-6b6b6b'>{dayMonth(l.at.slice(0, 10))}</span>}
                  {l.kind === 'system' ? (
                    <span className='my-1 text-center text-xs text-k-6b6b6b'>{l.text}</span>
                  ) : (
                    <div className={cn('flex flex-col', l.who === 'user' ? 'items-start' : 'items-end')}>
                      {l.showLabel && (
                        <span className='mb-0.5 text-[11px] font-bold text-k-6b6b6b'>
                          {l.who === 'user' ? first || 'The client' : l.who === 'ai' ? 'AI' : 'Agent'} · {clock(l.at)}
                        </span>
                      )}
                      <p
                        className={cn(
                          'm-0 max-w-[85%] rounded-2xl px-3.5 py-2 text-sm leading-[1.45] break-words whitespace-pre-wrap',
                          l.who === 'user' ? 'bg-k-f2f2f2 text-k-202020' : l.who === 'ai' ? 'bg-k-efeaff text-k-202020' : 'bg-kb-202020 text-on-btn'
                        )}
                      >
                        {l.text}
                      </p>
                    </div>
                  )}
                </div>
              ))
            )}
            {canReview && <div className='mt-4'>{reviewBlock}</div>}
          </section>
        ) : (
          <div className='flex flex-col gap-6 px-6 py-5'>
            {reviewFirst && reviewBlock}
            <section aria-label='Trip' className='flex flex-col gap-2'>
              <h3 className={h3}>Trip</h3>
              <div>
                <p className='m-0 text-[28px] leading-tight font-bold text-k-202020'>
                  {row.origin_code && row.destination_code ? `${row.origin_code} → ${row.destination_code}` : 'No route yet'}
                </p>
                {row.route_display && <p className='m-0 text-[13px] text-k-555555'>{row.route_display}</p>}
              </div>
              {trip.length > 0 && <Rows rows={trip} />}
            </section>

            <section aria-label='Level' className='flex flex-col gap-2'>
              <h3 className={h3}>Level</h3>
              <p className='m-0 text-[17px] text-k-202020'>
                <strong>{level.name}</strong> <span className='text-[13px] text-k-555555'>{level.why}</span>
              </p>
              <div className='flex gap-1' aria-hidden='true'>
                {[1, 2, 3, 4].map((n) => (
                  <span key={n} className={cn('h-1.5 flex-1 rounded-full', n <= level.steps ? 'bg-kb-202020' : 'bg-k-ececec')} />
                ))}
              </div>
              <ul className='m-0 flex list-none flex-col gap-1.5 p-0'>
                {checklist(row).map((c) => (
                  <li key={c.label} className='flex items-center gap-2.5 text-sm'>
                    <span
                      aria-hidden='true'
                      className={cn('flex size-[18px] flex-none items-center justify-center rounded-full', c.ok ? 'bg-k-00a645 text-on-btn' : 'bg-k-e9e9e9')}
                    >
                      {c.ok && <Check className='size-3' strokeWidth={3} />}
                    </span>
                    <span className={cn('flex-1', c.ok ? 'text-k-202020' : 'text-k-8a4b00')}>{c.label}</span>
                    {c.note && <span className='text-xs text-k-8a4b00'>{c.note}</span>}
                    <span className='sr-only'>{c.ok ? 'done' : 'missing'}</span>
                  </li>
                ))}
              </ul>
              {missingText(row) && <p className='m-0 text-[13px] font-bold text-k-8a4b00'>{missingText(row)}</p>}
              <p className='m-0 text-xs text-k-6b6b6b'>The level says what we know and what the client confirmed, not how likely they are to buy.</p>
            </section>

            {(about.persona || about.rows.length > 0) && (
              <section aria-label={`About ${first}`} className='flex flex-col gap-2'>
                <h3 className={h3}>About {first || 'the client'}</h3>
                {about.persona && (
                  <p className='m-0 text-sm text-k-202020'>
                    <strong>{about.persona.label}.</strong> {about.persona.why}
                  </p>
                )}
                {about.rows.length > 0 && <Rows rows={about.rows} />}
              </section>
            )}

            <section aria-label='Contact' className='flex flex-col gap-2'>
              <h3 className={h3}>Contact</h3>
              <Rows
                rows={[
                  { label: 'Phone', value: row.visitor_phone || '—' },
                  { label: 'E-mail', value: row.visitor_email || '—' },
                ]}
              />
            </section>

            {where.length > 0 && (
              <section aria-label='Where they came from' className='flex flex-col gap-2'>
                <h3 className={h3}>Where {first || 'they'} came from</h3>
                <Rows rows={where} />
              </section>
            )}

            {lead?.notes?.trim() && (
              <section aria-label='The assistant’s notes' className='flex flex-col gap-2'>
                <h3 className={h3}>The assistant’s notes</h3>
                <p className='m-0 rounded-2xl bg-k-f7f7f7 px-3.5 py-2.5 text-sm whitespace-pre-wrap text-k-202020'>{lead.notes.trim()}</p>
                <p className='m-0 text-xs text-k-6b6b6b'>Written by the assistant during the chat; they travel to the CRM with the lead.</p>
              </section>
            )}

            <section aria-label='CRM' className='flex flex-col gap-2'>
              <h3 className={h3}>CRM</h3>
              <p className={cn('m-0 flex items-center gap-2 text-[15px] font-bold', TONE_TEXT[crm.tone])}>
                <span aria-hidden='true' className={cn('size-2 rounded-full', TONE_DOT[crm.tone])} />
                {crm.text}
              </p>
              {row.crm_state !== 'in_crm' && (
                <p className='m-0 text-[13px] text-k-555555'>
                  {row.crm_state === 'no_phone'
                    ? 'The CRM needs a phone number. Without one the lead can’t be sent.'
                    : 'Closing a chat doesn’t send the lead. Send it now, or add it in the CRM yourself and save the lead number here.'}
                </p>
              )}
              {canPushNow && (
                <div className='flex flex-col gap-2'>
                  <button
                    type='button'
                    disabled={sending}
                    onClick={() => push.mutate({ leadId: id })}
                    className={cn(btn, 'self-start bg-kb-202020 text-on-btn')}
                  >
                    {sending ? 'Sending…' : 'Send to CRM'}
                  </button>
                  <form
                    className='flex flex-wrap items-center gap-2'
                    onSubmit={(e) => {
                      e.preventDefault()
                      if (manual.trim()) saveManual.mutate({ leadId: id, crmId: manual.trim() })
                    }}
                  >
                    <label htmlFor='lead-crm-id' className='text-[13px] text-k-555555'>
                      I added {first || 'them'} in the CRM, lead number
                    </label>
                    <input
                      id='lead-crm-id'
                      value={manual}
                      onChange={(e) => setManual(e.target.value)}
                      inputMode='numeric'
                      className='h-9 w-28 rounded-xl border border-k-c9c9c9 bg-k-ffffff px-3 text-sm text-k-202020'
                    />
                    <button type='submit' disabled={!manual.trim() || saveManual.isPending} className={cn(btn, 'border border-k-c9c9c9 text-k-202020')}>
                      Save
                    </button>
                  </form>
                </div>
              )}
            </section>

            {canLookUp && (
              <section aria-label='Earlier with us' className='flex flex-col gap-2'>
                <h3 className={h3}>Earlier with us</h3>
                {earlier.isLoading ? (
                  <p className='m-0 text-sm text-k-6b6b6b'>Looking up the phone and e-mail…</p>
                ) : earlier.isError ? (
                  <p className='m-0 text-sm text-k-8a4b00'>Couldn’t look up earlier chats.</p>
                ) : !earlier.data?.length ? (
                  <p className='m-0 text-sm text-k-555555'>No earlier chats with this phone or e-mail.</p>
                ) : (
                  <ul className='m-0 flex list-none flex-col p-0'>
                    {earlier.data.map((e) => (
                      <li key={e.conversation_id} className='flex items-center gap-3 border-t border-k-ececec py-2 text-sm first:border-t-0'>
                        <span className='w-16 text-k-555555'>{e.created_at ? dayMonth(e.created_at.slice(0, 10)) : ''}</span>
                        <span className='min-w-0 flex-1 text-k-202020'>
                          {e.route ?? 'No route'}
                          {e.in_crm && <span className='ms-2 text-xs text-k-0a6b35'>In the CRM{e.crm_lead_id ? ` · ${e.crm_lead_id}` : ''}</span>}
                        </span>
                        <Link to='/chats' search={{ highlight: e.conversation_id } as never} className='text-[13px] font-bold text-k-5b45e0 hover:underline'>
                          {e.chat_number ? `#${e.chat_number}` : 'Open'}
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            )}

            {!reviewFirst && reviewBlock}

            <section aria-label='History' className='flex flex-col gap-2 pb-4'>
              <h3 className={h3}>History</h3>
              <ul className='m-0 flex list-none flex-col gap-1.5 p-0'>
                {historyOf(row, reviewer).map((h) => (
                  <li key={`${h.at}-${h.text}`} className='flex gap-3 text-sm'>
                    <span className='w-[112px] flex-none text-k-555555'>{fmtWhen(h.at)}</span>
                    <span className='text-k-202020'>{h.text}</span>
                  </li>
                ))}
              </ul>
            </section>
          </div>
        )}
      </SheetContent>
    </Sheet>
  )
}
