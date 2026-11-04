import type { BrandConfig } from './types'

const config: BrandConfig = {
  id: 'bct',
  name: 'Business Class Tickets',
  logoUrl: 'https://bbc-widget.vercel.app/widget-logo-96.png',
  logoAlt: 'Business Class Tickets',
  tagline: 'Business class experts',
  formHeading: ['Talk to a business class', 'expert'],
  formLead: 'Leave your details, then ask anything about routes, dates or fares.',
  supportHeading: ['Help with your', 'booking'],
  supportLead: 'Tell us how to reach you and your ticket number.',
  ctaSubmit: 'Start chat',
  contactPhone: '+1 (888) 668-3009',
  contactEmail: 'info@businessclass-tickets.com',
  csPhone: '+1 (855) 811-3807',
  csEmail: 'cs@businessclass-tickets.com',
  websiteUrl: 'businessclass-tickets.com',
  closingMessage: 'Your flight request is confirmed! A travel specialist will contact you shortly. For immediate help, call +1 (888) 668-3009.',
  colors: {
    ink: '#0B1829',
    inkRgb: '11,24,41',
    headerBg: 'linear-gradient(100deg, #0B1829 0%, #14243A 55%, #22344F 100%)',
    headerRule: 'rgba(255,255,255,0.16)',
    accentOnInk: '#FFFFFF',
    accent: '#800020',
    cta: '#800020',
    ctaText: '#FFFFFF',
    ctaHover: '#66001A',
    launcher: '#0B1829',
    launcherRing: '#800020',
    launcherRingRgb: '128,0,32',
  },
}

export default config
