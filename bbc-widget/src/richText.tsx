import type { JSX } from 'preact'
// Turns the plain text of a message into text with working links: web addresses, email addresses and
// phone numbers. Built from strings and <a> elements only, never innerHTML, so message text can never
// inject markup. No regex lookbehind (older Safari cannot parse it and the whole widget would fail).

const URL_RE = /https?:\/\/[^\s<>"]+|www\.[^\s<>"]+/
const EMAIL_RE = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/
// A phone number written with separators: +1 (888) 322-7999, (888) 322-7999, 888-322-7999, +44 20 7946 0958 is
// not covered on purpose; bare digit runs (ticket numbers, booking references) never become links.
const PHONE_RE = /(?:\+\d{1,3}[\s.-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.-])\d{3}[\s.-]\d{4}/
const TOKEN_RE = new RegExp(`${URL_RE.source}|${EMAIL_RE.source}|${PHONE_RE.source}`, 'g')

function trimTrailing(token: string): string {
  // Sentence punctuation after a link is not part of it; a closing bracket is, if it has its opener.
  let t = token
  while (/[.,;:!?'")\]]$/.test(t)) {
    const last = t[t.length - 1]
    if (last === ')' && (t.match(/\(/g) || []).length >= (t.match(/\)/g) || []).length) break
    t = t.slice(0, -1)
  }
  return t
}

export function richText(text: string): (string | JSX.Element)[] {
  if (typeof text !== 'string' || !text) return []
  const out: (string | JSX.Element)[] = []
  let cursor = 0
  TOKEN_RE.lastIndex = 0
  let m: RegExpExecArray | null
  while ((m = TOKEN_RE.exec(text))) {
    const start = m.index
    const token = trimTrailing(m[0])
    if (!token) continue
    const before = text[start - 1]
    const after = text[start + token.length]
    const isUrl = /^(https?:\/\/|www\.)/i.test(token)
    const isEmail = !isUrl && token.includes('@')
    // Phone numbers must stand alone: no digit glued to either side.
    if (!isUrl && !isEmail && ((before && /\d/.test(before)) || (after && /\d/.test(after)))) continue
    if (start > cursor) out.push(text.slice(cursor, start))
    const href = isUrl
      ? (token.startsWith('www.') ? `https://${token}` : token)
      : isEmail
        ? `mailto:${token}`
        : `tel:${token.trim().startsWith('+') ? '+' : ''}${token.replace(/\D/g, '')}`
    out.push(
      <a key={start} href={href} {...(isUrl ? { target: '_blank', rel: 'noopener noreferrer' } : {})}>
        {token}
      </a>,
    )
    cursor = start + token.length
    TOKEN_RE.lastIndex = cursor
  }
  if (cursor < text.length) out.push(text.slice(cursor))
  return out
}
