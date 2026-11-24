import { useState } from 'react'
import { toast } from 'sonner'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ExternalLink } from 'lucide-react'
import { createCrmLead, isLockedPushRefusal, type ApiError } from '@/lib/api'
import type { CrmLeadStatus } from '@/lib/types'
import { cn } from '@/lib/utils'
import { crmButtonView } from '../view'

const NOTE_MAX = 2000

interface Props {
  conversationId: string
  status: CrmLeadStatus | undefined
}

/** "Create lead in CRM", under the chat on every screen (the CRM's dock included). The CRM makes the lead with
 *  its own rules, gives it to the agent who has the chat and keeps the chat as a comment; after that the row is
 *  the lead's link. Pressing twice is safe: the CRM keys the lead on the conversation. */
export function CrmLeadButton({ conversationId, status }: Props) {
  const queryClient = useQueryClient()
  const [noteOpen, setNoteOpen] = useState(false)
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const view = crmButtonView(status)

  const create = useMutation({
    mutationFn: (confirmLocked: boolean) => createCrmLead(conversationId, note, confirmLocked),
    onSuccess: (data) => {
      setError(null)
      setNote('')
      setNoteOpen(false)
      queryClient.setQueriesData({ queryKey: ['crm-lead', conversationId] }, data)
      // The lead row is now "in the CRM" with this number: the line above and the details follow.
      void queryClient.invalidateQueries({ queryKey: ['conversation', conversationId] })
    },
    onError: (err) => {
      if (isLockedPushRefusal(err)) {
        // Only ever the operator's own yes (same rule as Send to CRM, PR-15).
        toast.warning('This conversation was locked for abuse.', {
          duration: 10_000,
          action: { label: 'Create anyway', onClick: () => create.mutate(true) },
        })
        return
      }
      setError((err as ApiError)?.message || 'Couldn’t create the lead. Try again.')
    },
  })

  if (!view.show) return null

  if (view.linkText) {
    return (
      <div className='flex flex-col gap-1 px-1 text-[13px]'>
        {view.url ? (
          <a
            href={view.url}
            target='_blank'
            rel='noopener noreferrer'
            className='inline-flex w-fit items-center gap-1.5 font-bold text-k-5b45e0 hover:underline'
          >
            {view.linkText}
            <ExternalLink aria-hidden='true' className='size-3.5' />
          </a>
        ) : (
          <strong className='text-k-0a6b35'>{view.linkText.replace('Open lead in CRM', 'In the CRM')}</strong>
        )}
        {view.notes.map((n) => (
          <span key={n} className='text-k-8a4b00'>
            {n}
          </span>
        ))}
      </div>
    )
  }

  if (!view.canCreate) {
    return view.reason ? <div className='px-1 text-[13px] text-k-555555'>{view.reason}</div> : null
  }

  const busy = create.isPending
  return (
    <div className='flex flex-col gap-2 px-1'>
      <div className='flex flex-wrap items-center gap-x-3 gap-y-1.5'>
        <button
          type='button'
          onClick={() => {
            setError(null)
            create.mutate(false)
          }}
          disabled={busy}
          className='h-9 rounded-full bg-kb-202020 px-4 text-sm font-bold text-on-btn disabled:opacity-50'
        >
          {busy ? 'Creating the lead…' : 'Create lead in CRM'}
        </button>
        {!noteOpen && (
          <button
            type='button'
            onClick={() => setNoteOpen(true)}
            disabled={busy}
            className='min-h-8 text-sm font-bold text-k-5b45e0 hover:underline disabled:opacity-50'
          >
            Add a note
          </button>
        )}
      </div>
      {noteOpen && (
        <label className='flex flex-col gap-1 text-[13px] text-k-555555'>
          Note for the CRM (optional)
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value.slice(0, NOTE_MAX))}
            maxLength={NOTE_MAX}
            rows={3}
            disabled={busy}
            placeholder='What the consultant should know: dates they can move, airlines they want…'
            className={cn(
              'w-full resize-y rounded-[12px] border border-k-cfcfcf bg-k-ffffff px-3 py-2 text-sm text-k-202020',
              'placeholder:text-k-8a8a8a focus:border-k-202020 focus:outline-none disabled:opacity-60'
            )}
          />
        </label>
      )}
      {error && (
        <div role='alert' className='text-[13px] text-k-b3261e'>
          {error}
        </div>
      )}
    </div>
  )
}
