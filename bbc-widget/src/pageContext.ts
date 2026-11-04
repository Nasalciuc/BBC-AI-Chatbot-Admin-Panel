// What the visitor is looking at, read from the page's own title (its h1), so the greeting beside the
// corner button can speak to it and the chat can start from it. buybusinessclass.com titles its pages
// in a few fixed shapes (4 Nov 2026):
//   "New York to London business class flights"   route
//   "Business class flights to Abu Dhabi"          destination
//   "Business class flights from New York"         departure city
//   "Emirates business class deals"                airline
//   "First class flights to London"                (the same, in first class)
// Anything else, or another site, gets the general greeting.

export interface PageContext {
  /** The greeting's first line, a question. */
  title: string
  /** The greeting's second line. */
  line: string
  /** Typed into the message box when the chat opens from this page (never sent by itself). */
  draft: string
}

const clean = (s: string) => s.replace(/\s+/g, ' ').trim()

export function readPageContext(): PageContext {
  let h1 = ''
  try { h1 = clean(document.querySelector('h1')?.textContent || '') } catch { /* no DOM */ }
  if (h1.length > 90) h1 = ''

  let m: RegExpMatchArray | null
  if ((m = h1.match(/^(.+?) to (.+?) (business|first) class flights$/i))) {
    const [, from, to, cabin] = m
    return {
      title: `Flying ${from} to ${to}?`,
      line: `Tell me your dates and I'll check ${cabin.toLowerCase()} class fares.`,
      draft: `${from} to ${to}, `,
    }
  }
  if ((m = h1.match(/^(business|first) class flights to (.+)$/i))) {
    const [, cabin, to] = m
    return {
      title: `Flying to ${to}?`,
      line: `Tell me where from and when, and I'll check ${cabin.toLowerCase()} class fares.`,
      draft: `To ${to}, from `,
    }
  }
  if ((m = h1.match(/^(business|first) class flights from (.+)$/i))) {
    const [, cabin, from] = m
    return {
      title: `Flying from ${from}?`,
      line: `Tell me where to and when, and I'll check ${cabin.toLowerCase()} class fares.`,
      draft: `From ${from} to `,
    }
  }
  if ((m = h1.match(/^(.+?) (business|first) class deals$/i))) {
    const [, airline, cabin] = m
    return {
      title: `Looking at ${airline}?`,
      line: `Tell me your route and dates, and I'll check ${cabin.toLowerCase()} class fares.`,
      draft: `${airline}, `,
    }
  }
  return {
    title: 'Planning a business class trip?',
    line: "Tell me where and when, and I'll check fares for you.",
    draft: '',
  }
}
