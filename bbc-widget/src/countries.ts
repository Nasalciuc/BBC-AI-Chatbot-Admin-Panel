export interface Country {
  name: string
  code: string
  dial: string
  flag: string
  minDigits: number
  maxDigits: number
}

// Top BBC markets + broad E.164 coverage.
export const COUNTRIES: Country[] = [
  { name: 'United States', code: 'US', dial: '+1', flag: '🇺🇸', minDigits: 10, maxDigits: 10 },
  { name: 'United Kingdom', code: 'GB', dial: '+44', flag: '🇬🇧', minDigits: 10, maxDigits: 10 },
  { name: 'India', code: 'IN', dial: '+91', flag: '🇮🇳', minDigits: 10, maxDigits: 10 },
  { name: 'United Arab Emirates', code: 'AE', dial: '+971', flag: '🇦🇪', minDigits: 9, maxDigits: 9 },
  { name: 'Saudi Arabia', code: 'SA', dial: '+966', flag: '🇸🇦', minDigits: 9, maxDigits: 9 },
  { name: 'Canada', code: 'CA', dial: '+1', flag: '🇨🇦', minDigits: 10, maxDigits: 10 },
  { name: 'Australia', code: 'AU', dial: '+61', flag: '🇦🇺', minDigits: 9, maxDigits: 9 },
  { name: 'Singapore', code: 'SG', dial: '+65', flag: '🇸🇬', minDigits: 8, maxDigits: 8 },
  { name: 'Germany', code: 'DE', dial: '+49', flag: '🇩🇪', minDigits: 7, maxDigits: 11 },
  { name: 'France', code: 'FR', dial: '+33', flag: '🇫🇷', minDigits: 9, maxDigits: 9 },
  { name: 'Netherlands', code: 'NL', dial: '+31', flag: '🇳🇱', minDigits: 9, maxDigits: 9 },
  { name: 'Qatar', code: 'QA', dial: '+974', flag: '🇶🇦', minDigits: 8, maxDigits: 8 },
  { name: 'Kuwait', code: 'KW', dial: '+965', flag: '🇰🇼', minDigits: 8, maxDigits: 8 },
  { name: 'Hong Kong', code: 'HK', dial: '+852', flag: '🇭🇰', minDigits: 8, maxDigits: 8 },
  { name: 'Japan', code: 'JP', dial: '+81', flag: '🇯🇵', minDigits: 10, maxDigits: 10 },
  { name: 'China', code: 'CN', dial: '+86', flag: '🇨🇳', minDigits: 11, maxDigits: 11 },
  { name: 'South Africa', code: 'ZA', dial: '+27', flag: '🇿🇦', minDigits: 9, maxDigits: 9 },
  { name: 'Brazil', code: 'BR', dial: '+55', flag: '🇧🇷', minDigits: 11, maxDigits: 11 },
  { name: 'Mexico', code: 'MX', dial: '+52', flag: '🇲🇽', minDigits: 10, maxDigits: 10 },
  { name: 'Italy', code: 'IT', dial: '+39', flag: '🇮🇹', minDigits: 9, maxDigits: 11 },
  { name: 'Spain', code: 'ES', dial: '+34', flag: '🇪🇸', minDigits: 9, maxDigits: 9 },
  { name: 'Switzerland', code: 'CH', dial: '+41', flag: '🇨🇭', minDigits: 9, maxDigits: 9 },
  { name: 'Belgium', code: 'BE', dial: '+32', flag: '🇧🇪', minDigits: 9, maxDigits: 9 },
  { name: 'Sweden', code: 'SE', dial: '+46', flag: '🇸🇪', minDigits: 9, maxDigits: 9 },
  { name: 'Norway', code: 'NO', dial: '+47', flag: '🇳🇴', minDigits: 8, maxDigits: 8 },
  { name: 'Denmark', code: 'DK', dial: '+45', flag: '🇩🇰', minDigits: 8, maxDigits: 8 },
  { name: 'New Zealand', code: 'NZ', dial: '+64', flag: '🇳🇿', minDigits: 9, maxDigits: 9 },
  { name: 'Turkey', code: 'TR', dial: '+90', flag: '🇹🇷', minDigits: 10, maxDigits: 10 },
  { name: 'Israel', code: 'IL', dial: '+972', flag: '🇮🇱', minDigits: 9, maxDigits: 9 },
  { name: 'Romania', code: 'RO', dial: '+40', flag: '🇷🇴', minDigits: 9, maxDigits: 9 },
  { name: 'Belarus', code: 'BY', dial: '+375', flag: '🇧🇾', minDigits: 9, maxDigits: 9 },
  { name: 'Moldova', code: 'MD', dial: '+373', flag: '🇲🇩', minDigits: 8, maxDigits: 8 },
  { name: 'Ukraine', code: 'UA', dial: '+380', flag: '🇺🇦', minDigits: 9, maxDigits: 9 },
  { name: 'Kazakhstan', code: 'KZ', dial: '+7', flag: '🇰🇿', minDigits: 10, maxDigits: 10 },
  { name: 'Russia', code: 'RU', dial: '+7', flag: '🇷🇺', minDigits: 10, maxDigits: 10 },
  { name: 'Poland', code: 'PL', dial: '+48', flag: '🇵🇱', minDigits: 9, maxDigits: 9 },
  { name: 'Bulgaria', code: 'BG', dial: '+359', flag: '🇧🇬', minDigits: 8, maxDigits: 9 },
  { name: 'Croatia', code: 'HR', dial: '+385', flag: '🇭🇷', minDigits: 8, maxDigits: 9 },
  { name: 'Serbia', code: 'RS', dial: '+381', flag: '🇷🇸', minDigits: 8, maxDigits: 9 },
  { name: 'Slovakia', code: 'SK', dial: '+421', flag: '🇸🇰', minDigits: 9, maxDigits: 9 },
  { name: 'Slovenia', code: 'SI', dial: '+386', flag: '🇸🇮', minDigits: 8, maxDigits: 8 },
  { name: 'Estonia', code: 'EE', dial: '+372', flag: '🇪🇪', minDigits: 7, maxDigits: 8 },
  { name: 'Latvia', code: 'LV', dial: '+371', flag: '🇱🇻', minDigits: 8, maxDigits: 8 },
  { name: 'Lithuania', code: 'LT', dial: '+370', flag: '🇱🇹', minDigits: 8, maxDigits: 8 },
  { name: 'Armenia', code: 'AM', dial: '+374', flag: '🇦🇲', minDigits: 8, maxDigits: 8 },
  { name: 'Georgia', code: 'GE', dial: '+995', flag: '🇬🇪', minDigits: 9, maxDigits: 9 },
  { name: 'Azerbaijan', code: 'AZ', dial: '+994', flag: '🇦🇿', minDigits: 9, maxDigits: 9 },
  { name: 'Austria', code: 'AT', dial: '+43', flag: '🇦🇹', minDigits: 7, maxDigits: 11 },
  { name: 'Portugal', code: 'PT', dial: '+351', flag: '🇵🇹', minDigits: 9, maxDigits: 9 },
  { name: 'Greece', code: 'GR', dial: '+30', flag: '🇬🇷', minDigits: 10, maxDigits: 10 },
  { name: 'Czech Republic', code: 'CZ', dial: '+420', flag: '🇨🇿', minDigits: 9, maxDigits: 9 },
  { name: 'Hungary', code: 'HU', dial: '+36', flag: '🇭🇺', minDigits: 9, maxDigits: 9 },
  { name: 'Egypt', code: 'EG', dial: '+20', flag: '🇪🇬', minDigits: 10, maxDigits: 10 },
  { name: 'Nigeria', code: 'NG', dial: '+234', flag: '🇳🇬', minDigits: 10, maxDigits: 10 },
  { name: 'Kenya', code: 'KE', dial: '+254', flag: '🇰🇪', minDigits: 9, maxDigits: 9 },
  { name: 'Pakistan', code: 'PK', dial: '+92', flag: '🇵🇰', minDigits: 10, maxDigits: 10 },
  { name: 'Bangladesh', code: 'BD', dial: '+880', flag: '🇧🇩', minDigits: 10, maxDigits: 10 },
  { name: 'Sri Lanka', code: 'LK', dial: '+94', flag: '🇱🇰', minDigits: 9, maxDigits: 9 },
  { name: 'Philippines', code: 'PH', dial: '+63', flag: '🇵🇭', minDigits: 10, maxDigits: 10 },
  { name: 'Indonesia', code: 'ID', dial: '+62', flag: '🇮🇩', minDigits: 9, maxDigits: 12 },
  { name: 'Malaysia', code: 'MY', dial: '+60', flag: '🇲🇾', minDigits: 9, maxDigits: 10 },
  { name: 'Thailand', code: 'TH', dial: '+66', flag: '🇹🇭', minDigits: 9, maxDigits: 9 },
  { name: 'Vietnam', code: 'VN', dial: '+84', flag: '🇻🇳', minDigits: 9, maxDigits: 10 },
  { name: 'Argentina', code: 'AR', dial: '+54', flag: '🇦🇷', minDigits: 10, maxDigits: 10 },
  { name: 'Colombia', code: 'CO', dial: '+57', flag: '🇨🇴', minDigits: 10, maxDigits: 10 },
  { name: 'Chile', code: 'CL', dial: '+56', flag: '🇨🇱', minDigits: 9, maxDigits: 9 },
  { name: 'Peru', code: 'PE', dial: '+51', flag: '🇵🇪', minDigits: 9, maxDigits: 9 },
  { name: 'Morocco', code: 'MA', dial: '+212', flag: '🇲🇦', minDigits: 9, maxDigits: 9 },
  { name: 'Jordan', code: 'JO', dial: '+962', flag: '🇯🇴', minDigits: 9, maxDigits: 9 },
  { name: 'Lebanon', code: 'LB', dial: '+961', flag: '🇱🇧', minDigits: 8, maxDigits: 8 },
  { name: 'Bahrain', code: 'BH', dial: '+973', flag: '🇧🇭', minDigits: 8, maxDigits: 8 },
  { name: 'Oman', code: 'OM', dial: '+968', flag: '🇴🇲', minDigits: 8, maxDigits: 8 },
  { name: 'Iraq', code: 'IQ', dial: '+964', flag: '🇮🇶', minDigits: 10, maxDigits: 10 },
  { name: 'Finland', code: 'FI', dial: '+358', flag: '🇫🇮', minDigits: 9, maxDigits: 11 },
  { name: 'Ireland', code: 'IE', dial: '+353', flag: '🇮🇪', minDigits: 9, maxDigits: 9 },
  { name: 'Luxembourg', code: 'LU', dial: '+352', flag: '🇱🇺', minDigits: 9, maxDigits: 9 },
]

/**
 * Detect country from a full international number entered by user.
 * Uses longest matching dial code.
 */
export function detectCountryFromPhone(phone: string): Country | null {
  const raw = phone.trim()
  if (!raw) return null
  const digits = raw.replace(/\D/g, '')
  if (!digits) return null

  const sorted = [...COUNTRIES].sort(
    (a, b) => b.dial.replace(/\D/g, '').length - a.dial.replace(/\D/g, '').length
  )

  for (const country of sorted) {
    const dialDigits = country.dial.replace(/\D/g, '')
    if (digits.startsWith(dialDigits)) {
      return country
    }
  }
  return null
}

/**
 * Validate full phone number with country-specific local length range.
 */
export function validatePhone(phone: string, country: Country | null): boolean {
  if (!phone || !country) return false
  const dialDigits = country.dial.replace(/\D/g, '')
  const allDigits = phone.replace(/\D/g, '')
  if (!allDigits) return false
  if (!allDigits.startsWith(dialDigits)) return false
  const localDigits = allDigits.slice(dialDigits.length)
  return localDigits.length >= country.minDigits && localDigits.length <= country.maxDigits
}
