import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { toast } from 'sonner'
import { type ApiError, getTaskRules, saveTaskRules, tryTaskSentence } from '@/lib/api'
import type { TaskDefaultDue, TaskRules as Rules, TaskTry } from '@/lib/types'
import { cn } from '@/lib/utils'
import { Switch } from '@/components/ui/switch'
import { dayIn, clockIn, zoneCity } from '../view'

const card = 'flex flex-col gap-3 rounded-[20px] bg-k-ffffff p-5 ring-1 ring-k-ececec'
const h3 = 'm-0 text-[17px] font-bold text-k-202020'
const note = 'rounded-[12px] bg-k-f7f7f7 px-3 py-2 text-[13px] leading-[1.45] text-k-555555'

const DEFAULT_DUE: { key: TaskDefaultDue; label: string; text: string }[] = [
  { key: '1h', label: '1 hour', text: 'due in 1 hour' },
  { key: '2h', label: '2 hours', text: 'due in 2 hours' },
  { key: 'end_of_day', label: 'End of the shift', text: 'due at the end of the working day' },
  { key: 'next_morning', label: 'Next morning', text: 'due the next morning at 10:00' },
]

const HOURS = Array.from({ length: 24 }, (_, h) => `${String(h).padStart(2, '0')}:00`)

/** Tasks → Rules (owner, admin, dev): what makes a task, when it's due, what happens on Not ready. */
export function TaskRules() {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['task-rules'], queryFn: getTaskRules, select: (r) => r.data, retry: 1 })
  const save = useMutation({
    mutationFn: (patch: Partial<Rules>) => saveTaskRules(patch),
    onSuccess: (r) => {
      qc.setQueryData(['task-rules'], { success: true, data: { ...q.data, ...r.data } })
      toast.success('Saved. New messages follow the new rules.')
    },
    onError: (e) => toast.error((e as ApiError)?.message ?? 'Couldn’t save the rules. Try again.'),
  })
  const [sentence, setSentence] = useState('I’ll e-mail you the fare rules by 5')
  const [tried, setTried] = useState<TaskTry | null>(null)
  const tryIt = useMutation({
    mutationFn: () => tryTaskSentence(sentence),
    onSuccess: (r) => setTried(r.data),
    onError: (e) => toast.error((e as ApiError)?.message ?? 'Couldn’t check the sentence. Try again.'),
  })
  const [phrase, setPhrase] = useState('')

  if (q.isError) {
    const notSetUp = (q.error as ApiError)?.status === 503
    return (
      <div role='alert' className='flex flex-wrap items-center gap-3 rounded-2xl bg-k-fff3e0 px-4 py-3 text-sm text-k-8a4b00'>
        <span className='flex-1'>
          <strong>{notSetUp ? 'Tasks aren’t set up yet.' : 'Couldn’t load the rules.'}</strong>{' '}
          {notSetUp ? 'The database change (migration 051) hasn’t run.' : 'They aren’t shown rather than shown wrong.'}
        </span>
        {!notSetUp && (
          <button type='button' onClick={() => q.refetch()} className='h-9 rounded-full bg-kb-202020 px-4 text-[13px] font-bold text-on-btn'>
            Try again
          </button>
        )}
      </div>
    )
  }
  const r = q.data
  if (!r) return <div className='h-64 animate-pulse rounded-[20px] bg-k-f7f7f7' aria-busy='true' />

  const week = r.last_7_days ?? { caught: 0, not_a_promise: 0 }
  const share = week.caught ? Math.round((week.not_a_promise / week.caught) * 100) : 0
  const city = zoneCity(r.time_zone)
  const left = r.left_after_minutes ?? 10
  const due = DEFAULT_DUE.find((d) => d.key === r.default_due) ?? DEFAULT_DUE[1]
  const addPhrase = () => {
    const p = phrase.trim()
    if (p.length < 3 || r.phrases.some((x) => x.toLowerCase() === p.toLowerCase())) return
    save.mutate({ phrases: [...r.phrases, p] })
    setPhrase('')
  }

  return (
    <div className='grid grid-cols-1 gap-4 lg:grid-cols-2'>
      <section className={cn(card, 'bg-k-f7f7f7 ring-0')}>
        <h3 className={h3}>How strict we are right now</h3>
        <ul className='m-0 flex list-disc flex-col gap-1.5 ps-5 text-sm leading-[1.45] text-k-202020'>
          <li>
            <strong>Promises in chat:</strong> {r.catch_promises ? `become tasks. ${week.caught} in the last 7 days.` : 'not caught (off).'}
          </li>
          <li>
            <strong>No time said:</strong> {due.text}.
          </li>
          <li>
            <strong>Someone leaves the shift:</strong>{' '}
            {r.leave_shift === 'unowned' ? 'their tasks go to Unowned; the supervisor gives them out.' : 'their tasks go to whoever is Ready.'}
          </li>
          <li>
            <strong>Reminders:</strong> 15 min before, at the due time, then the supervisor after 30 min.
          </li>
          <li>
            <strong>Client asks to be called</strong> (while the AI has the chat): a call task, Unowned; the AI says a consultant will call.
          </li>
        </ul>
        <p className='m-0 text-xs text-k-6b6b6b'>Change them in the cards below. Try a sentence to see what it would make.</p>
      </section>

      <section className={card}>
        <h3 className={h3}>Try a sentence</h3>
        <form
          className='flex gap-2'
          onSubmit={(e) => {
            e.preventDefault()
            if (sentence.trim()) tryIt.mutate()
          }}
        >
          <label className='sr-only' htmlFor='task-try'>A sentence an agent writes</label>
          <input id='task-try' value={sentence} onChange={(e) => { setSentence(e.target.value); setTried(null) }} maxLength={500} className='h-11 min-w-0 flex-1 rounded-[12px] border border-k-c9c9c9 bg-k-ffffff px-3 text-[15px] text-k-202020' />
          <button type='submit' disabled={!sentence.trim() || tryIt.isPending} className='h-11 rounded-full bg-kb-202020 px-5 text-sm font-bold text-on-btn disabled:opacity-50'>
            Check
          </button>
        </form>
        {tried && (
          <p role='status' className={cn('m-0 rounded-[12px] px-3 py-2 text-sm', tried.makes_task ? 'bg-k-efeaff text-k-4a36c4' : 'bg-k-f7f7f7 text-k-555555')}>
            {tried.makes_task && tried.due_at ? (
              <>
                <strong>It makes a task:</strong> {tried.kind_label} · {dayIn(Date.parse(tried.due_at), r.time_zone)}, {clockIn(Date.parse(tried.due_at), r.time_zone)} · for the agent who wrote it
                {tried.due_kind === 'default' ? ' (no time said: the default)' : tried.due_words ? ` (“${tried.due_words}”)` : ''}
              </>
            ) : (
              <>
                <strong>No task.</strong> {tried.reason}
              </>
            )}
          </p>
        )}
        <p className='m-0 text-xs leading-[1.45] text-k-6b6b6b'>
          Words it understands for time: within the hour, in 30 minutes, half an hour, by 5, at 5:30, today, tonight, tomorrow, Monday… A clock time
          is read in the client’s departure city when we know it, otherwise in {city} time. Tasks shows each person their own clock.
        </p>
      </section>

      <section className={card}>
        <div className='flex items-start gap-3'>
          <div className='flex flex-1 flex-col gap-1'>
            <h3 className={h3}>Catch promises in chat</h3>
            <p className='m-0 text-sm text-k-555555'>When an agent writes that they will send, e-mail or call, a task is made for them, with the time they said.</p>
          </div>
          <Switch
            checked={r.catch_promises}
            disabled={save.isPending}
            onCheckedChange={(v) => save.mutate({ catch_promises: v })}
            aria-label='Catch promises in chat'
          />
        </div>
        <p className={note}>
          Last 7 days: {week.caught} caught, {week.not_a_promise} marked “Not a promise”{week.caught ? ` (${share}%)` : ''}. Over 20% would mean the
          phrases are too loose.
        </p>
      </section>

      <section className={card}>
        <h3 className={h3}>When the client asks to be called</h3>
        <p className='m-0 text-sm text-k-555555'>
          “Call me” while the AI has the chat: the AI answers that a consultant will call, and a call task is made. Nobody had the chat, so it goes to
          Unowned for the supervisor to give out.
        </p>
        <p className={note}>Always on: the AI’s promise always has a task behind it.</p>
      </section>

      <section className={card}>
        <h3 className={h3}>Due time when nobody says one</h3>
        <p className='m-0 text-sm text-k-555555'>Used when the message has no time in it, like “I’ll e-mail you shortly”.</p>
        <span role='radiogroup' aria-label='Due time when nobody says one' className='flex flex-wrap gap-1.5'>
          {DEFAULT_DUE.map((d) => (
            <button
              key={d.key}
              type='button'
              role='radio'
              aria-checked={r.default_due === d.key}
              disabled={save.isPending}
              onClick={() => save.mutate({ default_due: d.key })}
              className={cn('h-9 rounded-full px-3.5 text-[13px] font-bold', r.default_due === d.key ? 'bg-kb-202020 text-on-btn' : 'bg-k-f2f2f2 text-k-202020 hover:bg-k-ececec')}
            >
              {d.label}
            </button>
          ))}
        </span>
        <div className={cn(note, 'flex flex-wrap items-center gap-2')}>
          <span>Working hours: every day</span>
          <select aria-label='Working day starts' value={r.day_start} disabled={save.isPending} onChange={(e) => save.mutate({ day_start: e.target.value })} className='h-8 rounded-[8px] border border-k-c9c9c9 bg-k-ffffff px-1.5 text-[13px]'>
            {HOURS.map((h) => <option key={h} value={h}>{h}</option>)}
          </select>
          <span>–</span>
          <select aria-label='Working day ends' value={r.day_end} disabled={save.isPending} onChange={(e) => save.mutate({ day_end: e.target.value })} className='h-8 rounded-[8px] border border-k-c9c9c9 bg-k-ffffff px-1.5 text-[13px]'>
            {HOURS.map((h) => <option key={h} value={h}>{h}</option>)}
          </select>
          <span>, {city}. “Today” means by the end of it.</span>
        </div>
      </section>

      <section className={card}>
        <h3 className={h3}>When someone leaves the shift</h3>
        <p className='m-0 text-sm text-k-555555'>What happens to their open tasks when they go Not ready, or {left} minutes after they leave the panel.</p>
        <span role='radiogroup' aria-label='When someone leaves the shift' className='flex flex-col gap-1.5'>
          {([['unowned', 'Move them to Unowned. The supervisor gives them out.'], ['ready', 'Give them to whoever is Ready (the one with the fewest open tasks).']] as const).map(([key, text]) => (
            <button
              key={key}
              type='button'
              role='radio'
              aria-checked={r.leave_shift === key}
              disabled={save.isPending}
              onClick={() => save.mutate({ leave_shift: key })}
              className={cn('flex items-center gap-2.5 rounded-[12px] px-3 py-2.5 text-start text-sm font-bold text-k-202020', r.leave_shift === key ? 'ring-2 ring-k-202020' : 'bg-k-f7f7f7')}
            >
              <span aria-hidden='true' className={cn('size-4 flex-none rounded-full border-2', r.leave_shift === key ? 'border-[5px] border-k-202020' : 'border-k-9c9c9c')} />
              {text}
            </button>
          ))}
        </span>
      </section>

      <section className={card}>
        <h3 className={h3}>Reminders</h3>
        <p className='m-0 text-sm text-k-555555'>To the person 15 minutes before and at the due time. To the supervisor 30 minutes after.</p>
        <p className={note}>They come the way each person set alerts in Settings → Alerts: sound, browser notification, or both. Only while the panel is open.</p>
      </section>

      <section className={card}>
        <h3 className={h3}>Your CRM</h3>
        <p className='m-0 text-sm text-k-555555'>The panel sends leads to your CRM. Tasks stay here until the CRM can take them.</p>
        <ul className='m-0 flex list-none flex-col gap-1.5 p-0 text-[13px]'>
          <li className={note}>
            <strong className='text-k-202020'>Send each task to the CRM lead</strong> — needs a task endpoint in the CRM. Today it takes leads only.
          </li>
          <li className={note}>
            <strong className='text-k-202020'>Close here when the CRM marks it done</strong> — needs the CRM to send “done” back. Not there yet.
          </li>
        </ul>
        <p className='m-0 rounded-[12px] bg-k-fff3e0 px-3 py-2 text-[13px] text-k-8a4b00'>Agents mark tasks done here.</p>
      </section>

      <section className={cn(card, 'lg:col-span-2')}>
        <h3 className={h3}>The phrases that make a task</h3>
        <p className='m-0 text-sm text-k-555555'>
          Fixed phrases, checked on every message an agent sends. No AI, so the same message always gives the same task. “I’ll” also reads “I will”;
          “e-mail” also reads “email”. A question is never a promise.
        </p>
        <ul className='m-0 flex list-none flex-wrap gap-1.5 p-0'>
          {r.phrases.map((p) => (
            <li key={p} className='flex items-center gap-1 rounded-full bg-k-efeaff ps-3 pe-1 py-0.5 text-[13px] font-bold text-k-4a36c4'>
              {p}
              <button
                type='button'
                aria-label={`Remove “${p}”`}
                disabled={save.isPending || (r.phrases.length === 1 && r.catch_promises)}
                onClick={() => save.mutate({ phrases: r.phrases.filter((x) => x !== p) })}
                className='flex size-6 items-center justify-center rounded-full hover:bg-k-ffffff disabled:opacity-40'
              >
                <X aria-hidden='true' className='size-3.5' />
              </button>
            </li>
          ))}
        </ul>
        <form
          className='flex flex-wrap gap-2'
          onSubmit={(e) => {
            e.preventDefault()
            addPhrase()
          }}
        >
          <label className='sr-only' htmlFor='task-phrase'>Add a phrase</label>
          <input id='task-phrase' value={phrase} onChange={(e) => setPhrase(e.target.value)} maxLength={60} placeholder='let me send' className='h-10 w-[220px] rounded-[12px] border border-k-c9c9c9 bg-k-ffffff px-3 text-sm text-k-202020' />
          <button type='submit' disabled={phrase.trim().length < 3 || save.isPending} className='h-10 rounded-full border border-k-c9c9c9 px-4 text-[13px] font-bold text-k-202020 disabled:opacity-50'>
            + Add a phrase
          </button>
        </form>
        <p className='m-0 text-xs text-k-6b6b6b'>English. {week.caught} tasks made by these in the last 7 days.</p>
      </section>
    </div>
  )
}
