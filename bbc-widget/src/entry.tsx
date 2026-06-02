import { render } from 'preact'
import { Widget } from './Widget'
import { captureUtm } from './utm'
import brand from './config'

// Guard: prevent double-init if script included twice
if ((window as any).__BBC_WIDGET_LOADED__) {
  // already loaded
} else {
  (window as any).__BBC_WIDGET_LOADED__ = true
  captureUtm()

  // Read API URL from script data-api attribute
  const script = document.currentScript as HTMLScriptElement | null
    || document.querySelector('script[src*="bbc-widget"]') as HTMLScriptElement | null
  const apiUrl = script?.getAttribute('data-api')
    || 'https://admin-panel-error-production.up.railway.app'

  // Inject minimal CSS reset for widget elements
  const style = document.createElement('style')
  style.textContent = `
    #bbc-widget-root, #bbc-widget-root * {
      box-sizing: border-box;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      line-height: normal;
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
    }
  `
  document.head.appendChild(style)

  // Create container — DOMContentLoaded fallback if body not ready
  function init() {
    if (!document.body) {
      document.addEventListener('DOMContentLoaded', init)
      return
    }
    const root = document.createElement('div')
    root.id = 'bbc-widget-root'
    document.body.appendChild(root)

    const s = root.style
    s.setProperty('--bbc-primary', brand.colors.primary)
    s.setProperty('--bbc-primary-rgb', brand.colors.primaryRgb)
    s.setProperty('--bbc-header', brand.colors.header)
    s.setProperty('--bbc-header-text', brand.colors.headerText)
    s.setProperty('--bbc-user-bubble', brand.colors.userBubble)
    s.setProperty('--bbc-ai-bubble', brand.colors.aiBubble)
    s.setProperty('--bbc-ai-bubble-text', brand.colors.aiBubbleText)
    s.setProperty('--bbc-input-border', brand.colors.inputBorder)
    s.setProperty('--bbc-send-btn', brand.colors.sendButton)
    s.setProperty('--bbc-send-btn-text', brand.colors.sendButtonText)
    s.setProperty('--bbc-link', brand.colors.link)

    render(<Widget apiUrl={apiUrl} />, root)
  }
  init()
}
