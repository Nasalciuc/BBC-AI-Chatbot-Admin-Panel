/**
 * The CRM's chat dock opens on Conversations.
 *
 * The dock loads the panel's home with its sign-in token (/?sso_token=…,
 * docs/chatbot-sso.md). Since the redesign (#237) the dock shows no side menu
 * and no tab bar — there the panel is the chat list and nothing else — but the
 * home stayed the Dashboard: owners got the business overview, agents "Your
 * day", and nothing on screen led to Conversations.
 *
 * Source contracts rather than rendering: the route files and the layout are
 * .tsx with `@/` aliases, which `node --experimental-strip-types` cannot
 * resolve.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (p: string) => readFileSync(join(root, p), 'utf-8')

test('in the dock the panel has no menu of its own', () => {
  const layout = read('src/components/layout/authenticated-layout.tsx')
  assert.match(layout, /\{!embedded && <AppSidebar \/>\}/)
  assert.match(layout, /const tabBar = !embedded && /)
})

test('so in the dock the home is Conversations; in its own tab, the Dashboard', () => {
  const home = read('src/routes/_authenticated/index.tsx')
  assert.match(home, /import \{ isEmbedded \} from '@\/lib\/crm-bridge'/)
  // replace: Back must not return to the home, which would send it on again.
  assert.match(
    home,
    /beforeLoad: \(\) => \{\s*if \(isEmbedded\(\)\) throw redirect\(\{ to: '\/chats', replace: true \}\)\s*\}/,
  )
  assert.match(home, /component: DashboardForRole/)
})

test('Conversations never sends anyone home, or the dock would bounce between them', () => {
  const chats = read('src/routes/_authenticated/chats/index.tsx')
  assert.doesNotMatch(chats, /beforeLoad|redirect\(/)
})
