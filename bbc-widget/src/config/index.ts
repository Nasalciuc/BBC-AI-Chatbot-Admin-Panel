import bbc from './bbc'
import bct from './bct'
import type { BrandConfig } from './types'

const configs: Record<string, BrandConfig> = { bbc, bct }

/** Detect site from hostname at runtime. Fallback to VITE_BRAND or 'bbc'. */
function detectSite(): string {
  try {
    const h = window.location.hostname
    if (h.includes('businessclass-tickets')) return 'bct'
    if (h.includes('buybusinessclass')) return 'bbc'
  } catch { /* SSR / test env */ }
  return import.meta.env.VITE_BRAND || 'bbc'
}

const brand: BrandConfig = configs[detectSite()] || bbc

export default brand
export type { BrandConfig }
