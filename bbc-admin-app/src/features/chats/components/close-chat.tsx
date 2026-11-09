import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import type { CrmView } from '../view'

/**
 * "Close chat" with the CRM in view: closing never sends anything, so when
 * the client could be sent and isn't, the dialog says so and offers to send
 * first. The popover stays open on failure — a mute failure here left agents
 * believing chats were closed when they weren't.
 */
export function CloseChat({
  open,
  onOpenChange,
  first,
  sentence,
  crm,
  canSend,
  busy,
  onClose,
  onSendAndClose,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  first: string
  sentence: string
  crm: CrmView
  canSend: boolean
  busy: boolean
  onClose: () => void
  onSendAndClose: () => void
}) {
  const ready = crm.state === 'ready'
  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger asChild>
        <button
          type='button'
          className='h-10 flex-none rounded-full border border-kl-202020 bg-k-ffffff px-[18px] text-sm font-bold text-k-202020 hover:bg-k-f7f7f7 focus-visible:outline-2 focus-visible:outline-k-6ea8ff'
        >
          Close chat
        </button>
      </PopoverTrigger>
      <PopoverContent align='end' sideOffset={8} className='flex w-[300px] flex-col gap-3 rounded-2xl p-3.5' aria-label='Close this chat?'>
        <div className='text-sm leading-[1.45]'>
          <strong className='text-k-202020'>Close the chat with {first}?</strong>
          <br />
          <span className='text-k-555555'>{sentence}</span>
        </div>
        {ready && canSend && (
          <button
            type='button'
            onClick={onSendAndClose}
            disabled={busy}
            className='h-10 rounded-full bg-kb-202020 px-3.5 text-[13px] font-bold text-on-btn disabled:opacity-50'
          >
            {busy ? 'Sending…' : 'Send to CRM and close'}
          </button>
        )}
        <div className='flex flex-wrap justify-end gap-2'>
          <button
            type='button'
            onClick={() => onOpenChange(false)}
            className='h-9 rounded-full border border-k-c9c9c9 bg-k-ffffff px-3.5 text-[13px] font-bold text-k-202020'
          >
            Cancel
          </button>
          <button
            type='button'
            onClick={onClose}
            disabled={busy}
            className={
              ready && canSend
                ? 'h-9 rounded-full border border-k-c9c9c9 bg-k-ffffff px-3.5 text-[13px] font-bold text-k-202020 disabled:opacity-50'
                : 'h-9 rounded-full bg-kb-202020 px-3.5 text-[13px] font-bold text-on-btn disabled:opacity-50'
            }
          >
            {ready && canSend ? 'Close without sending' : 'Close chat'}
          </button>
        </div>
      </PopoverContent>
    </Popover>
  )
}
