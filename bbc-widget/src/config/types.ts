export interface BrandConfig {
  id: string
  /** The company's name as the panel's header shows it. */
  name: string
  logoUrl: string
  logoAlt: string
  /** One short line under the name in the header. */
  tagline: string
  /** Form intro title: the words, then the one accent word set apart in bold. */
  formHeading: [string, string]
  formLead: string
  supportHeading: [string, string]
  supportLead: string
  ctaSubmit: string
  // ── Site-specific contact info ──
  contactPhone: string
  contactEmail: string
  csPhone?: string
  csEmail?: string
  websiteUrl: string
  closingMessage: string
  /**
   * Who speaks in the panel: a name, said to be an AI (the 16 Nov debate, ADR 12). The server's
   * openings use the same name (bbc-chatbot-api/app/services/proactive.py, PERSONA): keep them equal.
   */
  persona: {
    name: string
    /** The letter in the round avatar when the page refuses Alex's photo (PersonaAvatar.tsx). */
    initial: string
    /** Under the name, before the brand: "AI travel agent · BuyBusinessClass". The word AI stays:
     *  the visitor is never led to think a person is typing. */
    role: string
  }
  /**
   * The panel's look (the BBC Chatbot canvas, ADR 16): one colour per site, a soft tint of it behind
   * the conversation. The corner button keeps `launcher*` below: the site draws a stand-in of it.
   */
  look: {
    /** Buttons, the visitor's bubbles, the avatar, links. White text on it passes 4.5:1. */
    primary: string
    primaryRgb: string
    primaryHover: string
    /** Text, dark and slightly tinted by the primary. */
    deep: string
    /** Quiet text: names, times, the line under the name. 4.5:1 on `soft`. */
    quiet: string
    /** The panel's ground: a very light tint of the primary. */
    soft: string
    /** Three blurred patches of colour behind the conversation (decor only). */
    blobs: [string, string, string]
  }
  // ── Theme ──
  colors: {
    /** The frame: visitor messages, the send button, focus rings. */
    ink: string
    inkRgb: string
    /** The panel's top (header, and the form's intro under it); a gradient is allowed. */
    headerBg: string
    /** The hairline under the panel's top. */
    headerRule: string
    /** The accent word in the form title, on the header's navy. */
    accentOnInk: string
    /** Links in messages and accents on white. */
    accent: string
    /** The form's main button. */
    cta: string
    ctaText: string
    ctaHover: string
    /** The round button in the corner. Kept equal to the site's stand-in for it. */
    launcher: string
    launcherRing: string
    launcherRingRgb: string
  }
}
