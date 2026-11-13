/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_BRAND: string
  readonly VITE_POSTHOG_PROJECT?: string
  readonly VITE_POSTHOG_HOST?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
