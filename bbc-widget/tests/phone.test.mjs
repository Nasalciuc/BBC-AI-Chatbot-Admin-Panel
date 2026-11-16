/**
 * The phone field of the contact form (countries.ts): the country shown while typing is the one the
 * number is sent with. The field used to start with "+", so a US number typed the usual way
 * ("404 555 0143") showed "RO +40 · Romania" until the send quietly made it +1 — and any ten digits went
 * out as +1, a Singapore number included.
 *
 * Plain .mjs + strip-types import: the widget's zero-dep test pattern.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { detectCountryFromPhone, normalizePhone, shownCountry, validatePhone } from '../src/countries.ts'

const form = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'TunnelForm.tsx'), 'utf8')

test('a US number typed the US way reads as the US at every keystroke, never Romania or Morocco', () => {
  for (const typed of ['404 555 0143', '(404) 555-0143', '404-555-0143', '212 555 0199', '305 555 0100', '415 555 0100']) {
    for (let i = 1; i <= typed.length; i++) {
      const part = typed.slice(0, i)
      if (!/\d/.test(part)) continue
      assert.equal(shownCountry(part)?.code, 'US', `"${part}"`)
    }
    const sent = normalizePhone(typed)
    assert.match(sent, /^\+1\d{10}$/)
    assert.equal(validatePhone(sent, detectCountryFromPhone(sent)), true)
  }
})

test('a number with + names its own country, the way it is sent', () => {
  const cases = [
    ['+44 7700 900123', 'GB', '+447700900123'],
    ['+40 721 234 567', 'RO', '+40721234567'],
    ['+1 (212) 555-0199', 'US', '+12125550199'],
    ['+34 612 345 678', 'ES', '+34612345678'],
    // Ten digits with "+": Singapore stays Singapore (the old rule sent it as +1 658…).
    ['+65 8123 4567', 'SG', '+6581234567'],
  ]
  for (const [typed, code, sent] of cases) {
    assert.equal(shownCountry(typed)?.code, code, typed)
    assert.equal(normalizePhone(typed), sent, typed)
    assert.equal(validatePhone(sent, detectCountryFromPhone(sent)), true, typed)
  }
})

test('eleven digits starting with 1 are a US number; too short is nothing yet', () => {
  assert.equal(normalizePhone('1 404 555 0143'), '+14045550143')
  assert.equal(shownCountry('14045550143')?.code, 'US')
  assert.equal(normalizePhone('555 01'), '')
  assert.equal(shownCountry(''), null)
  assert.equal(shownCountry('+'), null)
})

test('the form starts empty, shows the country of what it will send, and says how to write the number', () => {
  assert.match(form, /const \[phone, setPhone\] = useState\(''\)/)
  assert.doesNotMatch(form, /useState\('\+'\)/)
  assert.match(form, /import \{[^}]*normalizePhone, shownCountry[^}]*\} from '\.\/countries'/)
  assert.match(form, /setDetectedCountry\(shownCountry\(val\) \|\| COUNTRIES\[0\]\)/)
  // No "+" is put in front of what the visitor types any more.
  assert.doesNotMatch(form, /`\+\$\{trimmed\}`/)
  assert.match(form, /'A US number, or \+ and your country code'/)
})
