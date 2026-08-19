/**
 * An operator inside the CRM's iframe must not be able to sign themselves out.
 *
 * Their session arrived through SSO; they have no password to hand. Pressing
 * Sign out destroys it and leaves them looking at an empty frame in the middle
 * of a shift, with no way back in from there — the fix requires somebody else.
 *
 * Hidden, not disabled: a greyed-out button reads as "broken" and generates a
 * support ticket; an absent one raises no question at all.
 *
 * Source contracts rather than rendering: these are .tsx components with `@/`
 * aliases, which `node --experimental-strip-types` cannot resolve.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (p: string) => readFileSync(join(root, p), 'utf-8')

const MENUS = [
  'src/components/profile-dropdown.tsx',
  'src/components/layout/nav-user.tsx',
]

for (const file of MENUS) {
  test(`${file}: Sign out is hidden while embedded`, () => {
    const src = read(file)
    const guard = src.indexOf('!isEmbedded()')
    assert.ok(guard > 0, 'the menu must ask whether it is embedded')

    const signOut = src.indexOf('Sign out')
    assert.ok(signOut > guard, 'the guard must come before the item it hides')

    // Hidden, not disabled — a disabled control still invites a ticket.
    assert.ok(
      !/disabled=\{[^}]*isEmbedded/.test(src),
      'Sign out must be absent when embedded, not greyed out'
    )
  })

  test(`${file}: reuses isEmbedded from the bridge`, () => {
    const src = read(file)
    assert.match(src, /import \{ isEmbedded \} from '@\/lib\/crm-bridge'/)
    assert.ok(
      !src.includes('window.parent !== window'),
      'one detection, in crm-bridge.ts — never a second copy'
    )
  })
}

test('the dialog itself refuses to open while embedded', () => {
  // The last door: the menus are only two ways in, and a guard that lives
  // only in the menus is a guard somebody routes around.
  const src = read('src/components/sign-out-dialog.tsx')
  assert.match(src, /import \{ isEmbedded \} from '@\/lib\/crm-bridge'/)
  assert.match(src, /if \(isEmbedded\(\)\) return null/)

  const guard = src.indexOf('if (isEmbedded()) return null')
  const reset = src.indexOf('auth.reset()')
  assert.ok(guard < reset, 'the guard must sit above the code that ends the session')
})

test('standalone keeps Sign out exactly as it is', () => {
  // Nobody working outside the CRM feels any of this: the guard is a negation
  // of isEmbedded(), which is false in a normal tab, so every path is the old
  // path. Pinned by the absence of any other condition on these items.
  for (const file of MENUS) {
    const src = read(file)
    const item = src.slice(src.indexOf('!isEmbedded()'), src.indexOf('Sign out'))
    assert.ok(
      !/role|permission|canManage/.test(item),
      `${file}: embedding must be the ONLY thing that hides Sign out`
    )
  }
})

test('the shortcut label travels with the item it belongs to', () => {
  // ⇧⌘Q is only ever a label — nothing in src/ binds it — so hiding the item
  // removes the last mention of a sign-out path from an embedded panel.
  const src = read('src/components/profile-dropdown.tsx')
  const guard = src.indexOf('!isEmbedded()')
  assert.ok(src.indexOf('⇧⌘Q') > guard, 'the shortcut hint must be hidden too')
})
