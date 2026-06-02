export interface BrandConfig {
  id: string
  brandColor: string
  brandColorRgb: string
  headerColor: string
  logoUrl: string
  /** Logo for FAB on brand-color circle (higher contrast than header logo) */
  floatingLogoUrl?: string
  logoAlt: string
  formTitle: string
  formSubtitle: string
  chatTitle: string
  chatSubtitle: string
  ctaSubmit: string
  // ── Site-specific contact info ──
  contactPhone: string
  contactEmail: string
  csPhone?: string
  csEmail?: string
  websiteUrl: string
  closingMessage: string
  // ── Theme colors ──
  colors: {
    primary: string
    primaryRgb: string
    header: string
    headerText: string
    userBubble: string
    aiBubble: string
    aiBubbleText: string
    inputBorder: string
    sendButton: string
    sendButtonText: string
    link: string
  }
}
