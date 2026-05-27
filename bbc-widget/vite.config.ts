import { defineConfig, loadEnv } from 'vite'
import preact from '@preact/preset-vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const site = env.VITE_BRAND || 'bbc'

  return {
    plugins: [preact({ devtoolsInProd: false })],
    build: {
      lib: {
        entry: 'src/entry.tsx',
        name: site === 'bct' ? 'BCTWidget' : 'BBCWidget',
        formats: ['iife'],
        fileName: () => `${site}-widget.js`,
      },
      outDir: `dist/${site}`,
      rollupOptions: { output: { inlineDynamicImports: true } },
    },
  }
})
