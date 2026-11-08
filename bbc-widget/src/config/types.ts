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
