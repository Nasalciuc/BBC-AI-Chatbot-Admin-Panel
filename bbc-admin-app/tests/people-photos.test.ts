// People's photos — the redesign drew every person as initials and dropped
// the photos the old panel showed (Users, the account menu). They are back
// wherever a person's avatar is drawn; initials stay for no photo or a broken link.
// Runs with: node --experimental-strip-types --test tests/people-photos.test.ts

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, dirname, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')
const read = (p: string) => readFileSync(join(SRC, p), 'utf8')

function tsxFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) return tsxFiles(p)
    return p.endsWith('.tsx') ? [p] : []
  })
}

test('the avatar shows the photo and falls back to initials when there is none or it fails', () => {
  const src = read('components/person-avatar.tsx')
  // `url` is required, so no page can forget to pass it.
  assert.match(src, /url: string \| null \| undefined\n\s+tone\?: AvatarTone/)
  assert.match(src, /<AvatarPhoto url=\{url\} fallback=\{initials\(name\)\} \/>/)
  assert.match(src, /if \(!src \|\| failed === src\) return <>\{fallback\}<\/>/)
  assert.match(src, /onError=\{\(\) => setFailed\(src\)\}/)
  assert.match(src, /object-cover/)
})

test('every person avatar on every page passes the person’s photo', () => {
  const calls: string[] = []
  for (const file of tsxFiles(SRC)) {
    const src = readFileSync(file, 'utf8')
    for (const m of src.matchAll(/<PersonAvatar\b[^>]*\/>/g)) {
      calls.push(m[0])
      assert.match(m[0], /\surl=\{[^}]+\}/, `${relative(SRC, file)}: ${m[0]}`)
    }
  }
  // Users, the person panel, the roster, the floor (people and chats), a person's day, tasks, the dashboard.
  assert.ok(calls.length >= 8, `found only ${calls.length} avatars`)
})

test('the side menu and the phone bar show the signed-in person’s photo', () => {
  const side = read('components/layout/app-sidebar.tsx')
  assert.match(side, /photo=\{user\?\.avatar_url\}/)
  assert.match(side, /<AvatarPhoto url=\{photo\} fallback=\{initialsOf\(name\)\} \/>/)
  const phone = read('components/layout/mobile-tab-bar.tsx')
  assert.match(phone, /<AvatarPhoto url=\{user\?\.avatar_url\} fallback=\{initialsOf\(name\)\} \/>/)
})

test('the payloads without a user row carry the photo next to the name', () => {
  const types = read('lib/types.ts')
  const block = (name: string) => {
    const at = types.indexOf(`export interface ${name} {`)
    assert.ok(at >= 0, name)
    return types.slice(at, types.indexOf('\n}', at))
  }
  assert.match(block('AgentRow'), /avatar_url: string \| null/)
  assert.match(block('FloorPerson'), /avatar_url: string \| null/)
  assert.match(block('TaskRow'), /assignee_avatar_url: string \| null/)
  assert.match(block('PersonDay'), /avatar_url: string \| null/)
  assert.match(block('TeamNow'), /agent_avatar_url: string \| null/)
})
