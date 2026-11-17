/**
 * Which send a reply belongs to. Pure, so the rules run under `node --test` (tests/reply-turns.test.mjs).
 *
 * The server answers one message at a time, one reply per send, in order. It pushes the reply's
 * stream_end over SSE and only then answers the POST with {streaming: true}, and either can reach the
 * widget first: when the stream_end won, the answer marked a finished reply as streaming again and
 * "Alex is typing" stayed on screen (17 Nov 2026).
 *
 * A reply that comes in closes the oldest send still open — never whichever send happens to be the
 * newest when it lands (CodeRabbit #264): a late stream_end, or a reply the poll brings back while
 * SSE is down, does not close a newer send. A send whose answer says no reply streams (a person has
 * the chat, a ready-made answer, an error) closes itself.
 */
export interface ReplyTurns {
  /** A send starts: its number. */
  start(): number
  /** A reply came in (a stream_end, or an AI row the poll brought): the oldest open send is answered. */
  replyCameIn(): void
  /** Send `seq` expects no streamed reply: its answer said so, or it failed. */
  over(seq: number): void
  /** Send `seq` still waits for its reply: its answer may mark the reply streaming. */
  isOpen(seq: number): boolean
  /** Send `seq` is the latest: it alone unlocks sending or clears the stream. */
  isLatest(seq: number): boolean
}

export function createReplyTurns(): ReplyTurns {
  let sent = 0
  // In send order (a Set keeps insertion order): the first one is the oldest still open.
  const open = new Set<number>()
  return {
    start: () => {
      sent += 1
      open.add(sent)
      return sent
    },
    replyCameIn: () => {
      const oldest = open.values().next()
      if (!oldest.done) open.delete(oldest.value)
    },
    over: (seq) => {
      open.delete(seq)
    },
    isOpen: (seq) => open.has(seq),
    isLatest: (seq) => seq === sent,
  }
}
