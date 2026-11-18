/**
 * Where the agent writes: WhatsApp or email, the client's own contact (owner, 18 Nov 2026: "spune că îi
 * vom scrie, unde: pe email și WhatsApp" — no business number to message; the client leaves theirs).
 *
 * The buttons come from the server with what each does (`kind`), whatever the language of the label:
 * after the card's yes (WhatsApp, Email, "I'll wait here", Change details — A2), on the card itself and
 * on the pill after "Not now" (C1, C2), beside the refused quote form (D1) and in the message before a
 * contactless client leaves (B1). A WhatsApp number is placed in its country here, the way #3296's was
 * not ("04…" typed in Australia became an impossible US number): with + or 00 it names its own; without,
 * the country picked beside the field (guessed from the browser) and its trunk 0 dropped.
 *
 * The server's buttons are read in api.ts (readButtons). Pure (node-testable): the country list comes in
 * as an argument (countries.ts COUNTRIES).
 */
import type { Country } from './countries'

export type ChannelKind = 'whatsapp' | 'email'
export type ButtonKind = ChannelKind | 'wait' | 'change'

export interface ChatButton {
  kind: ButtonKind
  label: string
}

// The IANA zones of the countries most clients write from, when the browser's language names no region.
const TZ_COUNTRY: readonly (readonly [string, string])[] = [
  ['Australia/', 'AU'], ['Pacific/Auckland', 'NZ'], ['Europe/London', 'GB'], ['Europe/Dublin', 'IE'],
  ['Europe/Paris', 'FR'], ['Europe/Berlin', 'DE'], ['Europe/Madrid', 'ES'], ['Europe/Rome', 'IT'],
  ['Europe/Amsterdam', 'NL'], ['Europe/Brussels', 'BE'], ['Europe/Zurich', 'CH'], ['Europe/Stockholm', 'SE'],
  ['Europe/Oslo', 'NO'], ['Europe/Copenhagen', 'DK'], ['Europe/Istanbul', 'TR'], ['Asia/Jerusalem', 'IL'],
  ['Asia/Dubai', 'AE'], ['Asia/Riyadh', 'SA'], ['Asia/Qatar', 'QA'], ['Asia/Kuwait', 'KW'], ['Asia/Kolkata', 'IN'],
  ['Asia/Calcutta', 'IN'], ['Asia/Singapore', 'SG'], ['Asia/Hong_Kong', 'HK'], ['Asia/Tokyo', 'JP'],
  ['Asia/Shanghai', 'CN'], ['Africa/Johannesburg', 'ZA'], ['America/Sao_Paulo', 'BR'], ['America/Mexico_City', 'MX'],
  ['America/Toronto', 'CA'], ['America/Vancouver', 'CA'], ['America/', 'US'],
]

/** The country a number typed without its code most likely belongs to: the browser's region ("en-AU"),
 *  else its time zone, else the first of the list (the US: most of the sites' visitors). */
export function guessCountry(countries: readonly Country[], language: string, tz: string): Country {
  const region = /^[a-z]{2,3}[-_]([A-Za-z]{2})\b/.exec((language || '').trim())?.[1]?.toUpperCase()
  const byRegion = region ? countries.find(c => c.code === region) : undefined
  if (byRegion) return byRegion
  const zone = (tz || '').trim()
  const hit = TZ_COUNTRY.find(([prefix]) => zone === prefix || (prefix.endsWith('/') && zone.startsWith(prefix)))
  return (hit && countries.find(c => c.code === hit[1])) || countries[0]
}

const dialDigits = (c: Country) => c.dial.replace(/\D/g, '')

/** The country whose code a number in E.164 starts with (the longest code wins), or null. */
export function ownerOf(countries: readonly Country[], e164: string): Country | null {
  const digits = (e164 || '').replace(/\D/g, '')
  const fits = countries.filter(c => digits.startsWith(dialDigits(c)))
  return fits.sort((a, b) => dialDigits(b).length - dialDigits(a).length)[0] ?? null
}

/** Where the leading 0 is part of the number, dialled from abroad too: Italy's landlines ("02 1234 5678" is
 *  +39 02 1234 5678). The server keeps the same list (app/services/phone_country.py KEEPS_LEADING_ZERO). */
const KEEPS_LEADING_ZERO = new Set(['39'])

/** The number in E.164 for `country`, or '' when it cannot be one: "+61 412…" and "0061 412…" keep their
 *  own code; "0412 345 678" with Australia picked is +61412345678 (the trunk 0 dropped, but Italy's 0 kept);
 *  ten digits with the US picked are +1… */
export function phoneFor(value: string, country: Country): string {
  const raw = (value || '').trim()
  let digits = raw.replace(/\D/g, '')
  if (digits.length < 6) return ''
  if (raw.startsWith('+')) return `+${digits}`
  if (digits.startsWith('00')) return `+${digits.slice(2)}`
  const dial = dialDigits(country)
  if (dial === '1' && digits.length === 11 && digits.startsWith('1')) return `+${digits}`
  if (dial !== '1' && !KEEPS_LEADING_ZERO.has(dial)) digits = digits.replace(/^0+/, '')
  return `+${dial}${digits}`
}

/** A number the agent can write to: placed (phoneFor) and as long as its country's numbers are; a code the
 *  list does not know is taken at 8 to 15 digits. */
export function validWhatsapp(countries: readonly Country[], value: string, country: Country): string | null {
  const e164 = phoneFor(value, country)
  if (!e164) return null
  const owner = ownerOf(countries, e164)
  const digits = e164.slice(1)
  if (!owner) return digits.length >= 8 && digits.length <= 15 ? e164 : null
  const local = digits.length - dialDigits(owner).length
  return local >= owner.minDigits && local <= owner.maxDigits ? e164 : null
}

/** "+61412345678" written for reading: "+61 412 345 678" (the code, then groups of three). */
export function readablePhone(countries: readonly Country[], e164: string): string {
  const owner = ownerOf(countries, e164)
  const dial = owner ? owner.dial : '+'
  const rest = owner ? e164.slice(owner.dial.length) : e164.replace(/^\+/, '')
  const groups = rest.match(/.{1,3}/g) || [rest]
  if (groups.length > 1 && groups[groups.length - 1].length === 1) groups[groups.length - 2] += groups.pop()
  return `${dial}${owner ? ' ' : ''}${groups.join(' ')}`
}

export function validEmail(value: string): string | null {
  const v = (value || '').trim()
  return v.length <= 120 && /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v) ? v : null
}
