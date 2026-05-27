const { execSync } = require('child_process')
const { copyFileSync, mkdirSync, existsSync } = require('fs')
const { resolve } = require('path')

const adminDir = resolve(__dirname, '..')
const widgetDir = resolve(adminDir, '../bbc-widget')
const outDir = resolve(adminDir, 'public/widget')

mkdirSync(outDir, { recursive: true })

console.log('Installing widget dependencies...')
execSync('npm ci', { cwd: widgetDir, stdio: 'inherit' })

console.log('Building BBC + BCT widgets...')
execSync('npm run build:all', { cwd: widgetDir, stdio: 'inherit' })

const bbcSrc = resolve(widgetDir, 'dist', 'bbc', 'bbc-widget.js')
const bctSrc = resolve(widgetDir, 'dist', 'bct', 'bct-widget.js')

copyFileSync(bbcSrc, resolve(outDir, 'bbc-widget.js'))
console.log('Synced bbc-widget.js')

if (existsSync(bctSrc)) {
  copyFileSync(bctSrc, resolve(outDir, 'bct-widget.js'))
  console.log('Synced bct-widget.js')
} else {
  console.log('BCT widget not found — skipping (normal for BBC-only builds)')
}

console.log('Widget bundle sync complete')
