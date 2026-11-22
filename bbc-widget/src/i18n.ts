/**
 * The widget's own words in English, Spanish and French (ADR-9b, 18 Nov 2026). The card, its buttons and
 * every message after it are composed on the server in the browser's language
 * (bbc-chatbot-api app/services/proactive_i18n.py, card_words.py); these are the few labels the widget
 * draws itself around them: the field where the client leaves the contact the agent writes to, its
 * hints, and the line that goes out as their message. About 4% of Kayak visitors browse in another
 * language, Spanish and French 3% of them together.
 *
 * Pure (node-testable).
 */

export type Lang = 'en' | 'es' | 'fr'

/** "es" or "fr" from the browser's first language ("es-MX", "fr-CA"), else English. */
export function widgetLang(nav?: { language?: string } | null): Lang {
  let raw = ''
  try { raw = String((nav ?? (typeof navigator !== 'undefined' ? navigator : null))?.language || '') } catch { raw = '' }
  const code = raw.trim().toLowerCase().slice(0, 2)
  return code === 'es' || code === 'fr' ? code : 'en'
}

/** The browser's language as sent to the server ("es-MX"), or '' — the server keeps only what it speaks. */
export function browserLanguage(): string {
  try { return String(navigator.language || '').slice(0, 16) } catch { return '' }
}

/** The browser's time zone ("Australia/Sydney"), or '' — the server places a local phone number by it. */
export function timeZone(): string {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || '' } catch { return '' }
}

const WORDS = {
  en: {
    phoneLabel: 'Your mobile number',
    phoneHint: 'The agent texts the fares to this number.',
    emailLabel: 'Your email',
    emailHint: 'The agent sends the fares to this email.',
    priceLabel: 'Price you saw (optional)',
    send: 'Send',
    back: 'Back',
    country: 'Country code',
    badPhone: 'Check the number: it looks too short or too long.',
    badEmail: 'Check the email address.',
    sentSms: 'Text message',
    sentEmail: 'Email',
    sentPrice: 'Price seen',
    hide: 'Hide',
    // The field opened by itself (the owner's contact test): the other channel, one tap away.
    otherSms: 'Text me instead',
    otherEmail: 'Email me instead',
  },
  es: {
    phoneLabel: 'Su número de móvil',
    phoneHint: 'El agente le envía las tarifas por SMS a este número.',
    emailLabel: 'Su correo',
    emailHint: 'El agente le envía las tarifas a este correo.',
    priceLabel: 'Precio que vio (opcional)',
    send: 'Enviar',
    back: 'Volver',
    country: 'Código de país',
    badPhone: 'Revise el número: parece muy corto o muy largo.',
    badEmail: 'Revise el correo.',
    sentSms: 'SMS',
    sentEmail: 'Correo',
    sentPrice: 'Precio visto',
    hide: 'Ocultar',
    otherSms: 'Mejor por SMS',
    otherEmail: 'Mejor por correo',
  },
  fr: {
    phoneLabel: 'Votre numéro de mobile',
    phoneHint: "L'agent vous envoie les tarifs par SMS à ce numéro.",
    emailLabel: 'Votre e-mail',
    emailHint: "L'agent vous envoie les tarifs à cette adresse.",
    priceLabel: 'Prix vu (facultatif)',
    send: 'Envoyer',
    back: 'Retour',
    country: 'Indicatif',
    badPhone: 'Vérifiez le numéro : il semble trop court ou trop long.',
    badEmail: "Vérifiez l'adresse e-mail.",
    sentSms: 'SMS',
    sentEmail: 'E-mail',
    sentPrice: 'Prix vu',
    hide: 'Masquer',
    otherSms: 'Plutôt par SMS',
    otherEmail: 'Plutôt par e-mail',
  },
} as const

export type WordKey = keyof typeof WORDS.en

export function words(lang: Lang): Record<WordKey, string> {
  return WORDS[lang] || WORDS.en
}

/** What goes out as the client's message when they leave a contact on a channel — what the agent reads:
 *  "Text message: +61 412 345 678", "Email: client@example.com · Price seen: 4,200". The words are the
 *  server's button labels (app/services/card_words.py), so the chip and the message say the same. */
export function channelMessage(lang: Lang, kind: 'sms' | 'email', contact: string, price?: string): string {
  const w = words(lang)
  const sep = lang === 'fr' ? ' : ' : ': '
  const head = `${kind === 'sms' ? w.sentSms : w.sentEmail}${sep}${contact}`
  const seen = (price || '').trim()
  return seen ? `${head} · ${w.sentPrice}${sep}${seen}` : head
}
