import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { type ApiError, getTasksView, updateTask } from '@/lib/api'
import type { TaskPatchBody, TaskRow } from '@/lib/types'
import { localZone } from '@/features/tasks/view'

/**
 * This chat's tasks, for the "Follow-up caught" line under the agent's
 * message, the late banner and "came back to the chat". Quiet when Tasks
 * isn't set up (503) or the read fails: the chat itself never waits on it.
 */
export function useChatTasks(conversationId: string, meId: string | undefined) {
  const qc = useQueryClient()
  const q = useQuery({
    queryKey: ['chat-tasks', conversationId],
    queryFn: () => getTasksView({ conversationId }),
    select: (r) => r.data,
    enabled: !!conversationId,
    refetchInterval: 60_000,
    retry: (n, e) => (e as ApiError)?.status !== 503 && n < 1,
  })
  const tasks: TaskRow[] = [...(q.data?.tasks ?? []), ...(q.data?.done ?? [])]
  const byMessage = new Map<string, TaskRow>()
  for (const t of tasks) if (t.message_id) byMessage.set(t.message_id, t)
  // The banner: my promise in this chat that's late (the oldest first).
  const lateMine = (q.data?.tasks ?? [])
    .filter((t) => t.state === 'late' && t.assignee_id === meId)
    .sort((a, b) => Date.parse(a.due_at ?? '') - Date.parse(b.due_at ?? ''))[0] ?? null

  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: TaskPatchBody; ok: string }) => updateTask(id, body),
    onSuccess: (_r, v) => {
      qc.invalidateQueries({ queryKey: ['chat-tasks', conversationId] })
      qc.invalidateQueries({ queryKey: ['tasks'] })
      qc.invalidateQueries({ queryKey: ['notifications'] })
      toast.success(v.ok)
    },
    onError: (e) => toast.error((e as ApiError)?.message ?? 'Couldn’t save the task. Try again.'),
  })

  return {
    // The chat's own clock: the follow-up line sits next to the messages' times.
    tz: localZone(),
    byMessage,
    lateMine,
    busy: update.isPending,
    refresh: () => qc.invalidateQueries({ queryKey: ['chat-tasks', conversationId] }),
    done: (t: TaskRow) => update.mutate({ id: t.id, body: { done: true }, ok: 'Done.' }),
    later: (t: TaskRow, minutes: number) =>
      update.mutate({
        id: t.id,
        body: { due_at: new Date(Math.max(Date.now(), Date.parse(t.due_at ?? '') || 0) + minutes * 60_000).toISOString(), due_kind: 'picked' },
        ok: `Moved ${minutes} min later.`,
      }),
    notAPromise: (t: TaskRow) => update.mutate({ id: t.id, body: { not_a_promise: true }, ok: 'Removed: not a promise.' }),
  }
}
