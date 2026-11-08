import type { BrandConfig } from './types'

// The panel follows buybusinessclass.com's own design (its DESIGN.md): navy frame, the header's navy
// gradient under a gold hairline, gold as a detail, the form's main button in flat gold with navy words.
// The corner button keeps its exact look, because the site draws a stand-in of it before this script
// loads (FloatingContact.astro) and swaps it for this one without a visible change.
const config: BrandConfig = {
  id: 'bbc',
  name: 'BuyBusinessClass',
  logoUrl: 'https://bbc-widget.vercel.app/widget-logo-96.png',
  logoAlt: 'BuyBusinessClass',
  tagline: 'Business class experts',
  formHeading: ['Talk to a business class', 'expert'],
  formLead: 'Leave your details, then ask anything about routes, dates or fares.',
  supportHeading: ['Help with your', 'booking'],
  supportLead: 'Tell us how to reach you and your ticket number.',
  ctaSubmit: 'Start chat',
  contactPhone: '+1 (888) 322-7999',
  contactEmail: 'info@buybusinessclass.com',
  websiteUrl: 'buybusinessclass.com',
  closingMessage: 'Your flight request is confirmed! A travel consultant will contact you shortly. For immediate help, call +1 (888) 322-7999.',
  colors: {
    ink: '#0A1B41',
    inkRgb: '10,27,65',
    headerBg: 'linear-gradient(100deg, #0A1B41 0%, #11244C 55%, #203359 100%)',
    headerRule: 'rgba(211,176,116,0.6)',
    accentOnInk: '#D3B074',
    accent: '#7E5A2A',
    cta: '#D3B074',
    ctaText: '#0A1B41',
    ctaHover: '#C29A5C',
    launcher: '#0B1829',
    launcherRing: '#C9A54E',
    launcherRingRgb: '201,165,78',
  },
}

export default config
