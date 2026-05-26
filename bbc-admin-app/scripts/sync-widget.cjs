const { execSync } = require('child_process')
const { copyFileSync, mkdirSync } = require('fs')
const { resolve, dirname } = require('path')

const adminDir = resolve(__dirname, '..')
const widgetDir = resolve(adminDir, '../bbc-widget')
const outFile = resolve(adminDir, 'public/widget/bbc-widget.js')

mkdirSync(dirname(outFile), { recursive: true })

console.log('Installing widget dependencies...')
execSync('npm ci', { cwd: widgetDir, stdio: 'inherit' })

console.log('Building widget...')
execSync('npm run build', { cwd: widgetDir, stdio: 'inherit' })

copyFileSync(resolve(widgetDir, 'dist/bbc-widget.js'), outFile)
console.log('Widget bundle synced')
