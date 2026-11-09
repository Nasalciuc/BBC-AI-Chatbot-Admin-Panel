// What you were typing, per chat and per person, for this tab (sessionStorage):
// a reply meant for one client never follows you into another chat, a sign-in
// in the middle of a reply brings it back, and someone else signing in on
// this tab never sees it. Sending clears it.
// Pure apart from storage — pinned by tests/chat-drafts.test.ts.

export const DRAFT_PREFIX = 'bbc_draft:'

/** The id a draft is kept under: whose, and which chat. No person, no draft. */
export function draftId(operatorId: string | null | undefined, conversationId: string): string {
  return operatorId && conversationId ? `${operatorId}:${conversationId}` : ''
}

type Store = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

function store(): Store | null {
  try {
    return typeof sessionStorage === 'undefined' ? null : sessionStorage
  } catch {
    return null // storage blocked: drafts live only while the chat is open
  }
}

/** `id`: from draftId(). */
export function readDraft(id: string, s: Store | null = store()): string {
  if (!s || !id) return ''
  try {
    return s.getItem(DRAFT_PREFIX + id) ?? ''
  } catch {
    return ''
  }
}

/** Empty or whitespace-only removes the draft. */
export function writeDraft(id: string, text: string, s: Store | null = store()): void {
  if (!s || !id) return
  try {
    if (text.trim()) s.setItem(DRAFT_PREFIX + id, text)
    else s.removeItem(DRAFT_PREFIX + id)
  } catch {
    /* quota or blocked: the text stays in the composer while it's open */
  }
}

/** Signing out on purpose: nothing typed for a client stays in this tab for
 *  whoever uses the computer next. (A session that simply ran out keeps
 *  them — that's the point.) */
export function clearDrafts(s: Store & { length?: number; key?: (i: number) => string | null } | null = store()): void {
  if (!s || typeof s.key !== 'function' || typeof s.length !== 'number') return
  try {
    const keys: string[] = []
    for (let i = 0; i < s.length; i++) {
      const k = s.key(i)
      if (k && k.startsWith(DRAFT_PREFIX)) keys.push(k)
    }
    for (const k of keys) s.removeItem(k)
  } catch {
    /* storage blocked: nothing was kept */
  }
}
