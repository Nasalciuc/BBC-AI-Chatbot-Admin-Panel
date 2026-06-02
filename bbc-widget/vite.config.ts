import { defineConfig, loadEnv } from 'vite'
import preact from '@preact/preset-vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  const brand = env.VITE_BRAND || 'bbc'

  return {
    plugins: [preact({ devtoolsInProd: false })],
    build: {
      emptyOutDir: false,
      lib: {
        entry: 'src/entry.tsx',
        name: brand === 'bct' ? 'BCTWidget' : 'BBCWidget',
        formats: ['iife'],
        fileName: () => `${brand}-widget.js`,
      },
      outDir: 'dist',
      rollupOptions: { output: { inlineDynamicImports: true } },
    },
  }
})
