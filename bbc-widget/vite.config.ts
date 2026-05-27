import { defineConfig } from 'vite'
import preact from '@preact/preset-vite'

export default defineConfig({
  plugins: [preact({ devtoolsInProd: false })],
  build: {
    lib: {
      entry: 'src/entry.tsx',
      name: 'BCTWidget',
      formats: ['iife'],
      fileName: () => 'bbc-widget.js',
    },
    outDir: 'dist',
    rollupOptions: { output: { inlineDynamicImports: true } },
  },
})
