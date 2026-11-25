import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { LIVE_BOT_BASE, createLiveBotKey, getLiveBotKey, revokeLiveBotKey } from '@/lib/api'
import type { LiveBotKeyMade } from '@/lib/types'
import { cn } from '@/lib/utils'
import { keyDay } from '../view'

const btn = 'h-9 rounded-xl px-3.5 text-sm font-semibold disabled:opacity-50'
const quiet = 'bg-k-ffffff text-k-202020 shadow-[inset_0_0_0_1px_var(--k-e3e2e2)]'

/** The agent's own bot (the owner, 25 Nov 2026; bbc-chatbot-api app/services/live_bot.py): the key it works as them
 *  by. Shown to whoever may say hello. The key is shown once, when made; a new one stops the one before, Revoke stops
 *  it at once. */
export function BotKeyCard() {
  const qc = useQueryClient()
  const [made, setMade] = useState<LiveBotKeyMade | null>(null)
  const status = useQuery({ queryKey: ['live-bot-key'], queryFn: getLiveBotKey })
  const create = useMutation({
    mutationFn: createLiveBotKey,
    onSuccess: (res) => {
      setMade(res)
      qc.invalidateQueries({ queryKey: ['live-bot-key'] })
    },
    onError: (e: Error) => toast.error(`Couldn’t make a key: ${e.message}`),
  })
  const revoke = useMutation({
    mutationFn: revokeLiveBotKey,
    onSuccess: () => {
      setMade(null)
      toast.success('The key stopped working.')
      qc.invalidateQueries({ queryKey: ['live-bot-key'] })
    },
    onError: (e: Error) => toast.error(`Couldn’t revoke the key: ${e.message}`),
  })
  const active = status.data?.active === true
  const copy = (value: string) => {
    navigator.clipboard?.writeText(value).then(
      () => toast.success('Copied.'),
      () => toast.error('Couldn’t copy: select the key and copy it.'),
    )
  }

  return (
    <section aria-label='Your bot' className='flex flex-col gap-2.5 rounded-[20px] bg-k-f7f7f7 p-4'>
      <div className='flex flex-wrap items-center gap-2'>
        <b className='text-sm text-k-202020'>Your bot</b>
        <span className={cn('rounded-full px-2 py-0.5 text-xs font-medium',
          active ? 'bg-k-e3f6ea text-k-0a6b35' : 'bg-k-f2f2f2 text-k-555555')}>
          {status.isLoading ? 'Checking…' : status.isError ? 'Couldn’t check the key' : active
            ? `Key works until ${keyDay(status.data?.expires_at)}` : 'No key'}
        </span>
      </div>
      <p className='text-sm leading-[1.45] text-k-555555'>
        Connect your own bot with a key: it sees this list, says hello in your name with no limit of three, and answers
        as you in the chats its hellos start. Those chats are yours here too. No phone, e-mail, link or price from the bot.
      </p>
      {made && (
        <div className='flex flex-col gap-1.5'>
          <label className='flex flex-col gap-1 text-xs text-k-555555'>
            The key, shown this once
            <input readOnly value={made.key} onFocus={(e) => e.currentTarget.select()}
              className='h-9 rounded-xl border border-k-e3e2e2 bg-k-ffffff px-3 font-mono text-xs text-k-202020' />
          </label>
          <div>
            <button type='button' className={cn(btn, quiet)} onClick={() => copy(made.key)}>Copy the key</button>
          </div>
        </div>
      )}
      <div className='flex flex-wrap gap-2'>
        <button type='button' className={cn(btn, 'bg-kb-202020 text-on-btn')} disabled={create.isPending || status.isLoading}
          onClick={() => create.mutate()}>
          {active ? 'New key (stops the old one)' : 'Make a key'}
        </button>
        {active && (
          <button type='button' className={cn(btn, quiet)} disabled={revoke.isPending} onClick={() => revoke.mutate()}>
            Revoke
          </button>
        )}
      </div>
      <details className='text-xs text-k-6b6b6b'>
        <summary className='cursor-pointer'>What the bot calls</summary>
        <ul className='mt-1.5 flex flex-col gap-1 break-all font-mono'>
          <li>Authorization: Bearer &lt;key&gt;</li>
          <li>GET {LIVE_BOT_BASE}/visitors</li>
          <li>POST {LIVE_BOT_BASE}/hello {'{'}visitor_id, text?{'}'}</li>
          <li>GET {LIVE_BOT_BASE}/chats</li>
          <li>GET {LIVE_BOT_BASE}/chats/&lt;id&gt;/messages?after=</li>
          <li>POST {LIVE_BOT_BASE}/chats/&lt;id&gt;/messages {'{'}text{'}'}</li>
        </ul>
      </details>
    </section>
  )
}
