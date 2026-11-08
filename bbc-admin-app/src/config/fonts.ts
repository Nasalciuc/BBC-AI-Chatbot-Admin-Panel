/**
 * List of available font names (visit the url `/settings/appearance`).
 * This array is used to generate dynamic font classes (e.g., `font-inter`, `font-display`).
 *
 * BBC Typography (the chat panel design):
 * - 'helvetica' = Helvetica (default — the design's face)
 * - 'display'   = the system display face (SF Pro on Apple), used for page titles
 * - 'inter'     = Inter (kept selectable in Appearance)
 * - 'system'    = System default sans-serif
 *
 * 📝 How to Add a New Font (Tailwind v4+):
 * 1. Add the font name here.
 * 2. Update the `<link>` tag in 'index.html' to include the new font from Google Fonts (or any other source).
 * 3. Add the new font family to theme.css using the `@theme inline` and `font-family` CSS variable.
 */
export const fonts = ['helvetica', 'inter', 'display', 'system'] as const
