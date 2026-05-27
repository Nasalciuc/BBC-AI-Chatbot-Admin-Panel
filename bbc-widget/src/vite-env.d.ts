/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_BRAND: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
