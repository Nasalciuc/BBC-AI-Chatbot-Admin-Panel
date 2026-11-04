// The widget's one stylesheet, injected once into <head> by entry.tsx.
//
// Every rule is scoped under #bbc-widget-root, so it outranks the host site's element and class rules,
// and the reset below stops the site's own button/input/heading styles from leaking in. Brand colours
// arrive as custom properties set on the root from src/config (entry.tsx).
//
// Phones (under 768px): the panel is a full-screen sheet that slides up, as native chat apps do;
// fields are 16px so iOS never zooms on focus. Computers: a 400px panel rising from the corner button.

export const WIDGET_CSS = `
#bbc-widget-root {
  --bw-text: #0E1D3A;
  --bw-text-2: #36496B;
  --bw-muted: #56657F;
  --bw-placeholder: #6B7890;
  --bw-band: #F3F2EF;
  --bw-line: #E3DFD7;
  --bw-line-strong: #CFCAC0;
  --bw-paper: #FFFFFF;
  --bw-on-ink-2: #E3E8F0;
  --bw-danger: #B3261E;
  --bw-call: #0B7A5A;
  --bw-sans: 'Inter Variable', 'Inter', ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
  --bw-serif: 'Source Serif 4 Variable', 'Source Serif 4', Georgia, 'Times New Roman', serif;
  --bw-ease: cubic-bezier(0.22, 1, 0.36, 1);
  --bw-right: var(--fc-right, 24px);
  --bw-bottom: var(--fc-bottom, 24px);
  font: 400 15px/1.5 var(--bw-sans);
  color: var(--bw-text);
  text-align: left;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}
#bbc-widget-root, #bbc-widget-root *, #bbc-widget-root *::before, #bbc-widget-root *::after { box-sizing: border-box; }
#bbc-widget-root * {
  margin: 0; padding: 0; border: 0; border-radius: 0; background: none; box-shadow: none;
  font: inherit; color: inherit; letter-spacing: normal; text-transform: none; text-decoration: none;
  text-shadow: none; text-align: inherit; text-indent: 0; float: none; min-width: 0; max-width: none;
  -webkit-tap-highlight-color: transparent;
}
#bbc-widget-root img { display: block; }
#bbc-widget-root svg { display: block; flex-shrink: 0; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
#bbc-widget-root button { cursor: pointer; -webkit-appearance: none; appearance: none; }
#bbc-widget-root :focus { outline: none; }
#bbc-widget-root :focus-visible { outline: 2px solid var(--bw-ink); outline-offset: 2px; }
#bbc-widget-root ::selection { background: rgba(var(--bw-ink-rgb), 0.16); color: inherit; }
#bbc-widget-root .bw-sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }

/* ── The corner button and its greeting ─────────────────────────────── */
#bbc-widget-root .bw-launcher {
  position: fixed; right: 24px; bottom: 24px; z-index: 2147483000;
  display: flex; align-items: center;
}
#bbc-widget-root .bw-launch {
  position: relative; display: flex; align-items: center; justify-content: center;
  width: 60px; height: 60px; flex-shrink: 0; border-radius: 50%;
  background: var(--bw-launcher); border: 2px solid var(--bw-launcher-ring);
  box-shadow: 0 4px 20px rgba(var(--bw-launcher-ring-rgb), 0.25);
  transition: transform 0.2s var(--bw-ease), box-shadow 0.2s var(--bw-ease);
}
#bbc-widget-root .bw-launch:hover { transform: scale(1.05); box-shadow: 0 8px 28px rgba(var(--bw-launcher-ring-rgb), 0.35); }
#bbc-widget-root .bw-launch:active { transform: scale(0.97); }
#bbc-widget-root .bw-launch:focus-visible { outline-offset: 3px; }
#bbc-widget-root .bw-launch img { width: 30px; height: 30px; pointer-events: none; }

#bbc-widget-root .bw-nudge {
  position: absolute; right: calc(100% + 14px); bottom: -2px;
  display: flex; align-items: stretch; width: max-content; max-width: 290px;
  background: var(--bw-paper); color: var(--bw-text);
  box-shadow: 0 12px 32px rgba(10, 27, 65, 0.18), 0 0 0 1px rgba(10, 27, 65, 0.06);
  animation: bw-rise 0.5s var(--bw-ease) both;
}
#bbc-widget-root .bw-nudge__open { display: block; padding: 12px 4px 12px 16px; text-align: left; }
#bbc-widget-root .bw-nudge__q { display: block; font-size: 14px; line-height: 1.4; color: var(--bw-text-2); }
#bbc-widget-root .bw-nudge__a { display: flex; align-items: center; gap: 6px; margin-top: 2px; font-size: 15px; font-weight: 600; line-height: 1.35; color: var(--bw-ink); }
#bbc-widget-root .bw-nudge__a svg { width: 16px; height: 16px; transition: transform 0.2s var(--bw-ease); }
#bbc-widget-root .bw-nudge__open:hover .bw-nudge__a svg { transform: translateX(3px); }
#bbc-widget-root .bw-nudge__x { display: flex; justify-content: center; width: 40px; padding-top: 12px; color: var(--bw-muted); }
#bbc-widget-root .bw-nudge__x:hover { color: var(--bw-text); }
#bbc-widget-root .bw-nudge__x svg { width: 16px; height: 16px; }

/* ── The panel ───────────────────────────────────────────────────────── */
#bbc-widget-root .bw-panel {
  position: fixed; right: var(--bw-right); bottom: var(--bw-bottom); z-index: 2147483000;
  display: flex; flex-direction: column; overflow: hidden;
  width: 400px; max-width: calc(100vw - 32px);
  height: min(680px, calc(100vh - 48px)); height: min(680px, calc(100dvh - 48px));
  background: var(--bw-paper);
  box-shadow: 0 24px 48px rgba(10, 27, 65, 0.28), 0 0 0 1px rgba(10, 27, 65, 0.08);
  transform-origin: 100% 100%;
  animation: bw-panel-in 0.38s var(--bw-ease) both;
}
#bbc-widget-root .bw-panel--form { height: auto; max-height: calc(100vh - 48px); max-height: calc(100dvh - 48px); }
#bbc-widget-root .bw-panel--embedded {
  position: absolute; inset: 0; right: 0; bottom: 0; width: 100%; max-width: none;
  height: 100%; max-height: none; box-shadow: none; animation: none;
}

/* The top: the site header's navy gradient, under one hairline. */
#bbc-widget-root .bw-top { position: relative; flex-shrink: 0; background: var(--bw-header-bg); color: #FFFFFF; }
#bbc-widget-root .bw-top::after { content: ''; position: absolute; left: 0; right: 0; bottom: 0; height: 1px; background: var(--bw-header-rule); }
#bbc-widget-root .bw-head {
  display: flex; align-items: center; gap: 12px;
  min-height: 68px; padding: 12px 8px 12px 16px;
}
#bbc-widget-root .bw-head__mark { width: 40px; height: 40px; border-radius: 50%; box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.22); }
#bbc-widget-root .bw-head__text { flex: 1; min-width: 0; }
#bbc-widget-root .bw-head__name { font-size: 16px; font-weight: 600; line-height: 1.3; color: #FFFFFF; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#bbc-widget-root .bw-head__sub { font-size: 13px; line-height: 1.35; color: var(--bw-on-ink-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#bbc-widget-root .bw-head__actions { display: flex; align-items: center; gap: 2px; }
#bbc-widget-root .bw-iconbtn {
  display: flex; align-items: center; justify-content: center;
  width: 44px; height: 44px; border-radius: 50%; color: #FFFFFF;
  transition: background-color 0.18s var(--bw-ease);
}
#bbc-widget-root .bw-iconbtn:hover { background: rgba(255, 255, 255, 0.1); }
#bbc-widget-root .bw-iconbtn:focus-visible { outline-color: #FFFFFF; outline-offset: -3px; }
#bbc-widget-root .bw-iconbtn svg { width: 20px; height: 20px; }
#bbc-widget-root .bw-iconbtn--call:hover { background: none; }
#bbc-widget-root .bw-call {
  display: flex; align-items: center; justify-content: center;
  width: 36px; height: 36px; border-radius: 50%; background: var(--bw-call);
  transition: transform 0.18s var(--bw-ease), filter 0.18s var(--bw-ease);
}
#bbc-widget-root .bw-iconbtn--call:hover .bw-call { transform: scale(1.06); filter: brightness(1.08); }
#bbc-widget-root .bw-call svg { width: 17px; height: 17px; }

#bbc-widget-root .bw-intro { padding: 4px 24px 26px; }
#bbc-widget-root .bw-intro__title {
  font-family: var(--bw-serif); font-size: 27px; font-weight: 600; line-height: 1.2;
  color: #FFFFFF; text-wrap: balance;
}
#bbc-widget-root .bw-intro__title em { font-style: normal; font-weight: 700; color: var(--bw-accent-on-ink); }
#bbc-widget-root .bw-intro__lead { margin-top: 8px; font-size: 15px; line-height: 1.5; color: var(--bw-on-ink-2); max-width: 34ch; }

/* ── The form ────────────────────────────────────────────────────────── */
#bbc-widget-root .bw-form {
  flex: 1; overflow-y: auto; overscroll-behavior: contain;
  display: flex; flex-direction: column; gap: 18px; padding: 24px;
}
#bbc-widget-root .bw-field { display: flex; flex-direction: column; gap: 6px; }
#bbc-widget-root .bw-label { font-size: 14px; font-weight: 600; line-height: 1.3; color: var(--bw-text); }
#bbc-widget-root .bw-input {
  display: block; width: 100%; height: 48px; padding: 0 14px;
  font-size: 16px; line-height: 1.3; color: var(--bw-text); caret-color: var(--bw-ink);
  background: var(--bw-paper); border: 1px solid var(--bw-line-strong);
  -webkit-appearance: none; appearance: none;
  transition: border-color 0.15s var(--bw-ease), box-shadow 0.15s var(--bw-ease);
}
#bbc-widget-root .bw-input::placeholder { color: var(--bw-placeholder); opacity: 1; }
#bbc-widget-root .bw-input:hover { border-color: var(--bw-text-2); }
#bbc-widget-root .bw-input:focus { border-color: var(--bw-ink); box-shadow: inset 0 0 0 1px var(--bw-ink); }
#bbc-widget-root .bw-input:focus-visible { outline: none; }
#bbc-widget-root .bw-input[aria-invalid="true"] { border-color: var(--bw-danger); box-shadow: inset 0 0 0 1px var(--bw-danger); }
#bbc-widget-root .bw-phone {
  display: flex; align-items: stretch; height: 48px;
  background: var(--bw-paper); border: 1px solid var(--bw-line-strong);
  transition: border-color 0.15s var(--bw-ease), box-shadow 0.15s var(--bw-ease);
}
#bbc-widget-root .bw-phone:hover { border-color: var(--bw-text-2); }
#bbc-widget-root .bw-phone:focus-within { border-color: var(--bw-ink); box-shadow: inset 0 0 0 1px var(--bw-ink); }
#bbc-widget-root .bw-phone[data-invalid="true"] { border-color: var(--bw-danger); box-shadow: inset 0 0 0 1px var(--bw-danger); }
#bbc-widget-root .bw-phone__cc {
  display: flex; align-items: center; justify-content: center; min-width: 64px; padding: 0 12px;
  background: var(--bw-band); font-size: 14px; font-weight: 600; color: var(--bw-text-2);
  font-variant-numeric: tabular-nums; white-space: nowrap;
}
#bbc-widget-root .bw-phone .bw-input { flex: 1; height: 46px; border: 0; box-shadow: none; }
#bbc-widget-root .bw-hint { font-size: 13px; line-height: 1.4; color: var(--bw-muted); }
#bbc-widget-root .bw-error { display: flex; align-items: flex-start; gap: 6px; font-size: 13px; line-height: 1.4; color: var(--bw-danger); }
#bbc-widget-root .bw-error svg { width: 15px; height: 15px; margin-top: 1px; }
#bbc-widget-root .bw-cta {
  display: flex; align-items: center; justify-content: center; gap: 8px;
  width: 100%; height: 52px; margin-top: 4px;
  background: var(--bw-cta); color: var(--bw-cta-text);
  font-size: 16px; font-weight: 700; line-height: 1;
  transition: background-color 0.18s var(--bw-ease);
}
#bbc-widget-root .bw-cta:hover { background: var(--bw-cta-hover); }
#bbc-widget-root .bw-cta:active { transform: translateY(1px); }
#bbc-widget-root .bw-cta svg { width: 18px; height: 18px; transition: transform 0.2s var(--bw-ease); }
#bbc-widget-root .bw-cta:hover svg { transform: translateX(3px); }

/* ── The conversation ────────────────────────────────────────────────── */
#bbc-widget-root .bw-logwrap { position: relative; flex: 1; min-height: 0; display: flex; flex-direction: column; background: var(--bw-band); }
#bbc-widget-root .bw-log {
  flex: 1; overflow-y: auto; overscroll-behavior: contain;
  display: flex; flex-direction: column; padding: 20px 16px 16px;
  background: var(--bw-band);
  scrollbar-width: thin; scrollbar-color: var(--bw-line-strong) transparent;
}
#bbc-widget-root .bw-log::-webkit-scrollbar { width: 6px; }
#bbc-widget-root .bw-log::-webkit-scrollbar-thumb { background: var(--bw-line-strong); }
#bbc-widget-root .bw-row { display: flex; flex-direction: column; max-width: 86%; margin-top: 4px; animation: bw-msg-in 0.32s var(--bw-ease) both; }
#bbc-widget-root .bw-row--first { margin-top: 18px; }
#bbc-widget-root .bw-log > .bw-row:first-child { margin-top: 0; }
#bbc-widget-root .bw-row--in { align-self: flex-start; }
#bbc-widget-root .bw-row--out { align-self: flex-end; align-items: flex-end; }
#bbc-widget-root .bw-who { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; font-size: 13px; line-height: 1.3; color: var(--bw-muted); }
#bbc-widget-root .bw-who b { font-weight: 600; color: var(--bw-text); }
#bbc-widget-root .bw-ava { width: 28px; height: 28px; border-radius: 50%; object-fit: cover; background: var(--bw-band); }
#bbc-widget-root .bw-ava--brand { box-shadow: 0 0 0 1px var(--bw-line-strong); }
#bbc-widget-root .bw-ava--initials { display: flex; align-items: center; justify-content: center; background: var(--bw-ink); color: #FFFFFF; font-size: 12px; font-weight: 600; }
#bbc-widget-root .bw-msg { padding: 11px 15px; font-size: 15px; line-height: 1.5; white-space: pre-wrap; overflow-wrap: anywhere; }
#bbc-widget-root .bw-row--in .bw-msg { background: var(--bw-paper); color: var(--bw-text); }
#bbc-widget-root .bw-row--out .bw-msg { background: var(--bw-ink); color: #FFFFFF; }
#bbc-widget-root .bw-msg a { color: var(--bw-accent); font-weight: 600; text-decoration: underline; text-underline-offset: 3px; text-decoration-thickness: 1px; }
#bbc-widget-root .bw-row--out .bw-msg a { color: #FFFFFF; }
#bbc-widget-root .bw-msg a[href^="tel:"] { white-space: nowrap; }
#bbc-widget-root .bw-meta { margin-top: 5px; font-size: 13px; line-height: 1.3; color: var(--bw-muted); font-variant-numeric: tabular-nums; }
#bbc-widget-root .bw-note { align-self: center; max-width: 92%; margin-top: 18px; text-align: center; font-size: 13px; line-height: 1.45; color: var(--bw-muted); animation: bw-msg-in 0.32s var(--bw-ease) both; }
#bbc-widget-root .bw-note--error { color: var(--bw-danger); }
#bbc-widget-root .bw-day { align-self: center; margin-top: 22px; font-size: 13px; font-weight: 600; line-height: 1.3; color: var(--bw-muted); }
#bbc-widget-root .bw-log > .bw-day:first-child { margin-top: 0; }
#bbc-widget-root .bw-row--queued .bw-msg { opacity: 0.62; }
#bbc-widget-root .bw-retry { display: block; margin-left: auto; text-align: right; }
#bbc-widget-root .bw-retry u { text-decoration-thickness: 1px; text-underline-offset: 3px; }
#bbc-widget-root .bw-retry:hover u { text-decoration-thickness: 2px; }
#bbc-widget-root .bw-unseen {
  position: absolute; left: 50%; bottom: 12px; transform: translateX(-50%);
  display: flex; align-items: center; gap: 6px; height: 36px; padding: 0 14px;
  background: var(--bw-ink); color: #FFFFFF; font-size: 13px; font-weight: 600; white-space: nowrap;
  box-shadow: 0 12px 32px rgba(10, 27, 65, 0.22);
  animation: bw-unseen-in 0.28s var(--bw-ease) both;
}
#bbc-widget-root .bw-unseen svg { width: 15px; height: 15px; }
#bbc-widget-root .bw-note a { font-weight: 600; text-decoration: underline; text-underline-offset: 3px; text-decoration-thickness: 1px; white-space: nowrap; }
#bbc-widget-root .bw-meta--failed { color: var(--bw-danger); }
#bbc-widget-root .bw-dots { display: inline-flex; align-items: center; gap: 5px; padding: 16px; background: var(--bw-paper); }
#bbc-widget-root .bw-dots i { width: 6px; height: 6px; border-radius: 50%; background: var(--bw-muted); animation: bw-dot 1.2s infinite ease-in-out; }
#bbc-widget-root .bw-dots i:nth-child(2) { animation-delay: 0.15s; }
#bbc-widget-root .bw-dots i:nth-child(3) { animation-delay: 0.3s; }

#bbc-widget-root .bw-chips { flex-shrink: 0; display: flex; flex-wrap: wrap; gap: 8px; padding: 0 16px 16px; background: var(--bw-band); }
#bbc-widget-root .bw-chip {
  min-height: 44px; padding: 10px 14px; border: 1px solid var(--bw-ink);
  background: var(--bw-paper); color: var(--bw-ink);
  font-size: 14px; font-weight: 600; line-height: 1.3; text-align: left;
  transition: background-color 0.15s var(--bw-ease), color 0.15s var(--bw-ease);
}
#bbc-widget-root .bw-chip:hover { background: var(--bw-ink); color: #FFFFFF; }

#bbc-widget-root .bw-compose {
  flex-shrink: 0; display: flex; align-items: flex-end; gap: 8px;
  padding: 12px 16px; background: var(--bw-paper);
}
#bbc-widget-root .bw-compose .bw-input {
  flex: 1; height: 48px; min-height: 48px; max-height: 136px; padding: 13px 14px;
  line-height: 1.35; resize: none; overflow-y: auto;
}
#bbc-widget-root .bw-send {
  display: flex; align-items: center; justify-content: center;
  width: 48px; height: 48px; flex-shrink: 0;
  background: var(--bw-ink); color: #FFFFFF;
  transition: background-color 0.18s var(--bw-ease), color 0.18s var(--bw-ease);
}
#bbc-widget-root .bw-send:hover:not(:disabled) { background: #203359; }
#bbc-widget-root .bw-send:active:not(:disabled) { transform: scale(0.94); }
#bbc-widget-root .bw-send { transition: background-color 0.18s var(--bw-ease), color 0.18s var(--bw-ease), transform 0.12s var(--bw-ease); }
#bbc-widget-root .bw-send:disabled { background: var(--bw-band); color: #8A95A8; cursor: default; }
#bbc-widget-root .bw-send svg { width: 20px; height: 20px; }

/* ── Phones: a full-screen sheet ─────────────────────────────────────── */
@media (max-width: 767px) {
  #bbc-widget-root .bw-panel,
  #bbc-widget-root .bw-panel--form {
    top: var(--bw-vvt, 0px); left: 0; right: 0; bottom: auto; width: 100%; max-width: none;
    height: 100%; height: var(--bw-vvh, 100dvh); max-height: none;
    box-shadow: none; animation-name: bw-sheet-in; animation-duration: 0.34s;
  }
  #bbc-widget-root .bw-head { padding-top: max(12px, env(safe-area-inset-top)); }
  #bbc-widget-root .bw-intro { padding: 4px 20px 28px; }
  #bbc-widget-root .bw-intro__title { font-size: clamp(25px, 8vw, 32px); }
  /* The form screen: a navy field on top with the title low, the form under it near the thumb. On a short
     phone the whole sheet scrolls, so the button is never cut in half. */
  #bbc-widget-root .bw-panel--form { overflow-y: auto; overscroll-behavior: contain; }
  #bbc-widget-root .bw-panel--form .bw-top { flex: 1 0 auto; display: flex; flex-direction: column; justify-content: space-between; }
  #bbc-widget-root .bw-panel--form .bw-form { flex: 0 0 auto; overflow: visible; padding: 24px 20px max(24px, env(safe-area-inset-bottom)); }
  #bbc-widget-root .bw-compose { padding: 10px 12px max(10px, env(safe-area-inset-bottom)); }
  #bbc-widget-root .bw-msg { font-size: 16px; }
  #bbc-widget-root .bw-nudge { right: 0; bottom: calc(100% + 12px); max-width: min(300px, calc(100vw - 32px)); }
}
@media (max-width: 767px) and (max-height: 620px) {
  #bbc-widget-root .bw-head { min-height: 60px; padding-top: 8px; padding-bottom: 8px; }
  #bbc-widget-root .bw-intro { padding-bottom: 18px; }
  #bbc-widget-root .bw-panel--form .bw-form { gap: 12px; padding-top: 16px; padding-bottom: 16px; }
}
#bbc-widget-root .bw-panel--embedded { animation: none; }

/* The chat replacing the form in an open panel: the frame stays, the conversation fades in. */
#bbc-widget-root .bw-panel.bw-panel--swap { animation: none; }
#bbc-widget-root .bw-panel--swap .bw-logwrap,
#bbc-widget-root .bw-panel--swap .bw-compose { animation: bw-fade 0.26s var(--bw-ease) both; }
@keyframes bw-fade { from { opacity: 0; } to { opacity: 1; } }
#bbc-widget-root .bw-closing .bw-panel { animation: bw-panel-out 0.2s cubic-bezier(0.4, 0, 1, 1) both; }
@media (max-width: 767px) {
  #bbc-widget-root .bw-closing .bw-panel { animation: bw-sheet-out 0.22s cubic-bezier(0.4, 0, 1, 1) both; }
}
@keyframes bw-panel-out { to { opacity: 0; transform: translateY(12px) scale(0.98); } }
@keyframes bw-sheet-out { to { transform: translateY(100%); } }
@keyframes bw-unseen-in { from { opacity: 0; transform: translate(-50%, 8px); } to { opacity: 1; transform: translate(-50%, 0); } }
@keyframes bw-panel-in { from { opacity: 0; transform: translateY(16px) scale(0.97); } to { opacity: 1; transform: none; } }
@keyframes bw-sheet-in { from { transform: translateY(100%); } to { transform: none; } }
@keyframes bw-rise { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
@keyframes bw-msg-in { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
@keyframes bw-dot { 0%, 80%, 100% { opacity: 0.3; } 40% { opacity: 1; } }

@media (prefers-reduced-motion: reduce) {
  #bbc-widget-root *, #bbc-widget-root *::before, #bbc-widget-root *::after { animation: none !important; transition: none !important; }
}
`
