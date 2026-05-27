const { execSync } = require('child_process')
const { copyFileSync, mkdirSync } = require('fs')
const { resolve, dirname } = require('path')

const adminDir = resolve(__dirname, '..')
const widgetDir = resolve(adminDir, '../bbc-widget')
const outDir = resolve(adminDir, 'public/widget')

mkdirSync(outDir, { recursive: true })

console.log('Installing widget dependencies...')
execSync('npm ci', { cwd: widgetDir, stdio: 'inherit' })

console.log('Building BBC + BCT widgets...')
execSync('npm run build:all', { cwd: widgetDir, stdio: 'inherit' })

copyFileSync(resolve(widgetDir, 'dist/bbc-widget.js'), resolve(outDir, 'bbc-widget.js'))
copyFileSync(resolve(widgetDir, 'dist/bct-widget.js'), resolve(outDir, 'bct-widget.js'))
console.log('Widget bundles synced (bbc-widget.js + bct-widget.js)')
