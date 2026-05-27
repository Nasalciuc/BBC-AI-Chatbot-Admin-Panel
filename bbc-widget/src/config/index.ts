import bbc from './bbc'
import bct from './bct'
import type { BrandConfig } from './types'

const configs: Record<string, BrandConfig> = { bbc, bct }
const brand: BrandConfig = configs[import.meta.env.VITE_BRAND || 'bbc'] || bbc

export default brand
export type { BrandConfig }
