// Continuity (owner, 4 Nov 2026): "when I am typing a message and the AI is typing and I close the
// box, when I open it back again no message is there". Closing used to unmount the chat: its connection
// closed, a message waiting for the reply to finish was lost, the draft was dropped, and a reply that
// landed while closed had nobody listening. These source contracts pin the fix. Zero dependencies.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')
const widget = readFileSync(join(SRC, 'Widget.tsx'), 'utf8')
const chat = readFileSync(join(SRC, 'ChatWindow.tsx'), 'utf8')
const buttons = readFileSync(join(SRC, 'FloatingButtons.tsx'), 'utf8')

test('a conversation stays mounted while the chat is closed, on every page', () => {
  assert.match(widget, /const chatMounted = step === 'chat' \|\| \(!embedded && sessionActive\)/)
  assert.match(widget, /\{chatMounted && \(/)
  assert.match(widget, /visible=\{step === 'chat'\}/)
})

test('a closed chat is not a present client: no heartbeat, a minimized close, open again on reopen', () => {
  const ping = chat.slice(chat.indexOf('const sendPing = () => {'))
  assert.ok(ping.slice(0, 400).includes('if (!visibleRef.current) return'), 'pings pause while closed')
  const vis = chat.slice(chat.indexOf('const wasVisibleRef = useRef(visible)'))
  assert.ok(vis.includes("notifySessionClose('minimized', true)"), 'closing reports minimized')
  assert.ok(vis.indexOf('/open`') > -1, 'reopening reports open')
})

test('what the visitor typed and what waits its turn survive closing and other pages', () => {
  assert.match(chat, /useState<string>\(\(\) => ssGet\(DRAFT_KEY\)/)
  assert.match(chat, /useEffect\(\(\) => \{ ssSet\(DRAFT_KEY, input\) \}, \[input\]\)/)
  assert.match(chat, /ssGet\(QUEUE_KEY\)/)
  assert.match(chat, /ssSet\(QUEUE_KEY, queued\.length \? JSON\.stringify\(queued\) : null\)/)
})

test('unread counts only the assistant and people, and only what arrived after it was last seen', () => {
  assert.match(chat, /\(m\.role === 'ai' \|\| m\.role === 'agent'\) && timeOfMsg\(m\) > \(seen as number\)/)
  // History already there when nothing was recorded counts as read (no badge for old messages).
  assert.match(chat, /if \(seen === null\) \{[\s\S]{0,200}writeSeen\(convId, latest\)/)
})

test('the corner button carries the news: a count, a preview, a chime for a live arrival', () => {
  assert.match(buttons, /bw-launch__badge/)
  assert.match(buttons, /bw-nudge--message/)
  assert.match(widget, /if \(u\.fresh\) playChime\(\)/)
  // The round button stays the first button: the site's hand-over clicks `#bbc-floating-btn button`.
  const root = buttons.indexOf('id="bbc-floating-btn"')
  assert.ok(buttons.indexOf('class="bw-launch"', root) < buttons.indexOf('class="bw-nudge', root))
})
