// The widget's one stylesheet, injected once into <head> by entry.tsx.
//
// Every rule is scoped under #bbc-widget-root, so it outranks the host site's element and class rules,
// and the reset below stops the site's own button/input/heading styles from leaking in. Brand colours
// arrive as custom properties set on the root from src/config (entry.tsx).
//
// The look is the BBC Chatbot canvas's visitor widget (the 16 Nov debate, ADR 16): one colour per site
// (blue on buybusinessclass.com, burgundy on businessclass-tickets.com), a soft tint of it behind the
// conversation, Alex's round avatar, rounded bubbles, pill buttons under the last message and a rounded
// composer with a round send button. The corner button is the exception: the site draws a stand-in of it
// before this script loads, so it keeps its exact look.
//
// Phones (under 768px): the panel is a full-screen sheet that slides up, as native chat apps do;
// fields are 16px or more so iOS never zooms on focus. Computers: a 400px panel rising from the corner
// button (the form 440px), or, from the button in its top, the whole screen with bigger text (largeView.ts).

export const WIDGET_CSS = `
#bbc-widget-root {
  --bw-text: var(--bw-deep, #0E1D3A);
  --bw-text-2: var(--bw-deep, #36496B);
  --bw-muted: var(--bw-quiet, #56657F);
  --bw-placeholder: var(--bw-quiet, #6B7890);
  --bw-band: var(--bw-soft, #F3F5FA);
  --bw-line: rgba(var(--bw-primary-rgb, 30,79,216), 0.12);
  --bw-line-strong: rgba(var(--bw-primary-rgb, 30,79,216), 0.24);
  --bw-paper: #FFFFFF;
  --bw-glass: rgba(255, 255, 255, 0.9);
  --bw-glass-edge: rgba(255, 255, 255, 0.95);
  --bw-lift: 0 1px 2px rgba(16, 24, 40, 0.06), 0 0 0 1px rgba(var(--bw-primary-rgb, 30,79,216), 0.06);
  --bw-danger: #B3261E;
  --bw-live: #16A34A;
  --bw-sans: 'Inter Variable', 'Inter', ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
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
#bbc-widget-root :focus-visible { outline: 2px solid var(--bw-primary, var(--bw-ink)); outline-offset: 2px; }
#bbc-widget-root ::selection { background: rgba(var(--bw-primary-rgb, 30,79,216), 0.16); color: inherit; }
#bbc-widget-root .bw-sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }

/* ── The corner button: kept exactly as the site's stand-in draws it ───── */
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

#bbc-widget-root .bw-launch__badge {
  position: absolute; top: -4px; right: -4px;
  display: flex; align-items: center; justify-content: center;
  min-width: 22px; height: 22px; padding: 0 6px; border-radius: 999px;
  background: #C8261C; color: #FFFFFF; box-shadow: 0 0 0 2px #FFFFFF;
  font-size: 12px; font-weight: 700; line-height: 1; font-variant-numeric: tabular-nums;
  animation: bw-pop 0.36s var(--bw-ease) both;
}
#bbc-widget-root .bw-launch__pulse {
  position: absolute; inset: -2px; border-radius: 50%; pointer-events: none;
  box-shadow: 0 0 0 2px var(--bw-launcher-ring);
  animation: bw-pulse 1.1s cubic-bezier(0.22, 1, 0.36, 1) 2 both;
}

/* ── Beside the button: the greeting, a new message, the proactive card ── */
#bbc-widget-root .bw-nudge {
  position: absolute; right: calc(100% + 14px); bottom: -2px;
  display: flex; align-items: flex-start; width: 300px; border-radius: 20px; overflow: hidden;
  background: rgba(255, 255, 255, 0.97); color: var(--bw-text);
  -webkit-backdrop-filter: blur(14px); backdrop-filter: blur(14px);
  box-shadow: 0 14px 44px rgba(16, 24, 40, 0.18), 0 0 0 1px rgba(16, 24, 40, 0.06);
  animation: bw-rise 0.5s var(--bw-ease) both;
}
#bbc-widget-root .bw-nudge__open { display: flex; flex-direction: column; align-items: flex-start; flex: 1; min-width: 0; padding: 14px 4px 16px 16px; text-align: left; }
#bbc-widget-root .bw-nudge__who { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; font-size: 13px; line-height: 1.3; color: var(--bw-muted); }
#bbc-widget-root .bw-nudge__who b { font-weight: 700; color: var(--bw-text); }
#bbc-widget-root .bw-nudge__id { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
#bbc-widget-root .bw-nudge__id b { font-size: 15px; line-height: 1.25; letter-spacing: -0.01em; }
#bbc-widget-root .bw-nudge__id small { font-size: 12px; line-height: 1.3; color: var(--bw-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#bbc-widget-root .bw-nudge__ava { width: 32px; height: 32px; border-radius: 50%; object-fit: cover; flex-shrink: 0; }
#bbc-widget-root .bw-nudge__ava--brand { box-shadow: 0 0 0 1px var(--bw-line-strong); }
#bbc-widget-root .bw-nudge__ava--initials,
#bbc-widget-root .bw-nudge__ava--persona {
  display: flex; align-items: center; justify-content: center;
  background: var(--bw-primary, var(--bw-ink)); color: #FFFFFF; font-size: 14px; font-weight: 700;
  box-shadow: 0 0 0 3px rgba(var(--bw-primary-rgb, 30,79,216), 0.12);
}
#bbc-widget-root .bw-nudge__title { font-size: 16px; font-weight: 700; line-height: 1.35; color: var(--bw-text); letter-spacing: -0.01em; }
#bbc-widget-root .bw-nudge__text {
  margin-top: 2px; font-size: 14px; line-height: 1.5; color: var(--bw-text);
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden;
}
#bbc-widget-root .bw-nudge__a { display: flex; align-items: center; gap: 6px; margin-top: 10px; font-size: 14px; font-weight: 600; color: var(--bw-primary, var(--bw-ink)); }
#bbc-widget-root .bw-nudge__cta {
  display: flex; align-items: center; gap: 8px; margin-top: 14px; min-height: 44px; padding: 0 18px; border-radius: 999px;
  background: var(--bw-primary, var(--bw-ink)); color: #FFFFFF; font-size: 14px; font-weight: 600;
  transition: background-color 0.18s var(--bw-ease);
}
#bbc-widget-root .bw-nudge__open:hover .bw-nudge__cta { background: var(--bw-primary-hover, #203359); }
#bbc-widget-root .bw-nudge__a svg, #bbc-widget-root .bw-nudge__cta svg { width: 16px; height: 16px; transition: transform 0.2s var(--bw-ease); }
#bbc-widget-root .bw-nudge__open:hover .bw-nudge__a svg, #bbc-widget-root .bw-nudge__open:hover .bw-nudge__cta svg { transform: translateX(3px); }
#bbc-widget-root .bw-nudge__x { display: flex; justify-content: center; width: 44px; height: 44px; padding-top: 14px; color: var(--bw-muted); }
#bbc-widget-root .bw-nudge__x:hover { color: var(--bw-text); }
#bbc-widget-root .bw-nudge__x svg { width: 16px; height: 16px; }

/* The proactive card (phones): Alex, the visitor's own search in his words and two buttons, above the
   corner button. It covers nothing else; the chat opens on a tap only. */
#bbc-widget-root .bw-nudge--card { flex-direction: column; align-items: stretch; width: 330px; }
#bbc-widget-root .bw-nudge--card .bw-nudge__open { padding: 16px 16px 4px; }
#bbc-widget-root .bw-nudge--card .bw-nudge__text { -webkit-line-clamp: 6; font-size: 15px; }
#bbc-widget-root .bw-card__actions { display: flex; align-items: center; gap: 8px; padding: 12px 16px 16px; }
#bbc-widget-root .bw-card__yes {
  flex: 1; min-height: 44px; padding: 10px 18px; border-radius: 999px;
  background: var(--bw-primary, var(--bw-ink)); color: #FFFFFF; font-size: 15px; font-weight: 600; line-height: 1.3;
  box-shadow: 0 6px 16px rgba(var(--bw-primary-rgb, 30,79,216), 0.24);
  transition: background-color 0.18s var(--bw-ease);
}
#bbc-widget-root .bw-card__yes:hover { background: var(--bw-primary-hover, #203359); }
#bbc-widget-root .bw-card__no { min-height: 44px; padding: 10px 14px; border-radius: 999px; color: var(--bw-muted); font-size: 14px; font-weight: 600; }
#bbc-widget-root .bw-card__no:hover { color: var(--bw-text); background: rgba(var(--bw-primary-rgb, 30,79,216), 0.06); }
/* C1: the card's own channel (WhatsApp on a phone, email on a computer) under the yes, "Not now" beside it. */
#bbc-widget-root .bw-card__actions--channel { flex-wrap: wrap; }
#bbc-widget-root .bw-card__actions--channel .bw-card__yes { flex: 1 1 100%; }
#bbc-widget-root .bw-card__channel {
  flex: 1; display: inline-flex; align-items: center; justify-content: center; gap: 8px;
  min-height: 44px; padding: 10px 16px; border-radius: 999px;
  border: 1px solid rgba(var(--bw-primary-rgb, 30,79,216), 0.3); background: #FFFFFF;
  color: var(--bw-primary, var(--bw-ink)); font-size: 14px; font-weight: 600; line-height: 1.3;
}
#bbc-widget-root .bw-card__channel:hover { background: rgba(var(--bw-primary-rgb, 30,79,216), 0.06); }
#bbc-widget-root .bw-card__channel svg { flex-shrink: 0; }
/* C2: the pill after "Not now" — small, to the left of the button (on a phone too: the site keeps its own
   floating buttons above ours), until ✕ or a tap. */
#bbc-widget-root .bw-pill {
  position: absolute; right: calc(100% + 12px); bottom: 8px;
  display: flex; align-items: center; border-radius: 999px; white-space: nowrap;
  background: rgba(255, 255, 255, 0.97); color: var(--bw-primary, var(--bw-ink));
  box-shadow: 0 8px 24px rgba(16, 24, 40, 0.16), 0 0 0 1px rgba(16, 24, 40, 0.06);
  animation: bw-rise 0.4s var(--bw-ease) both;
}
#bbc-widget-root .bw-pill__open { display: flex; align-items: center; gap: 8px; min-height: 44px; padding: 0 6px 0 16px; font-size: 14px; font-weight: 600; }
#bbc-widget-root .bw-pill__x { display: flex; align-items: center; justify-content: center; width: 40px; height: 44px; color: var(--bw-muted); }
#bbc-widget-root .bw-pill__x:hover { color: var(--bw-text); }
#bbc-widget-root .bw-pill__x svg { width: 15px; height: 15px; }
@media (max-width: 767px) {
  #bbc-widget-root .bw-nudge--card { right: 0; bottom: calc(100% + 12px); width: min(340px, calc(100vw - 48px)); }
}

/* ── The panel ───────────────────────────────────────────────────────── */
#bbc-widget-root .bw-panel {
  position: fixed; right: var(--bw-right); bottom: var(--bw-bottom); z-index: 2147483000;
  display: flex; flex-direction: column; overflow: hidden;
  width: 400px; max-width: calc(100vw - 32px);
  height: min(680px, calc(100vh - 48px)); height: min(680px, calc(100dvh - 48px));
  border-radius: 24px;
  background:
    radial-gradient(260px 240px at 0% 0%, var(--bw-blob-1, transparent), transparent 72%),
    radial-gradient(260px 260px at 100% 22%, var(--bw-blob-2, transparent), transparent 72%),
    radial-gradient(300px 260px at 85% 100%, var(--bw-blob-3, transparent), transparent 72%),
    var(--bw-soft, #F4F7FE);
  color: var(--bw-text);
  box-shadow: 0 24px 56px rgba(16, 24, 40, 0.22), 0 0 0 1px rgba(16, 24, 40, 0.06);
  transform-origin: 100% 100%;
  animation: bw-panel-in 0.38s var(--bw-ease) both;
}
#bbc-widget-root .bw-panel.bw-panel--hidden { display: none !important; }
#bbc-widget-root .bw-panel--form { width: 440px; height: auto; max-height: calc(100vh - 48px); max-height: calc(100dvh - 48px); }
#bbc-widget-root .bw-panel--embedded {
  position: absolute; inset: 0; right: 0; bottom: 0; width: 100%; max-width: none;
  height: 100%; max-height: none; border-radius: 0; box-shadow: none; animation: none;
}

/* The top: Alex's photo, his line, a call button and close. */
#bbc-widget-root .bw-top { position: relative; flex-shrink: 0; color: var(--bw-text); }
#bbc-widget-root .bw-head {
  display: flex; align-items: center; gap: 12px;
  min-height: 72px; padding: 14px 10px 10px 16px;
}
#bbc-widget-root .bw-head__ava {
  position: relative; display: flex; align-items: center; justify-content: center; flex-shrink: 0;
  width: 42px; height: 42px; border-radius: 50%;
  background: var(--bw-primary, var(--bw-ink)); color: #FFFFFF; font-size: 17px; font-weight: 700;
  box-shadow: 0 0 0 3px rgba(255, 255, 255, 0.85);
}
#bbc-widget-root .bw-head__live {
  position: absolute; right: -1px; bottom: -1px; width: 12px; height: 12px; border-radius: 50%;
  background: var(--bw-live); box-shadow: 0 0 0 2px #FFFFFF;
}
#bbc-widget-root .bw-head__text { flex: 1; min-width: 0; }
#bbc-widget-root .bw-head__name { font-size: 17px; font-weight: 700; line-height: 1.25; letter-spacing: -0.01em; color: var(--bw-text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
/* Two lines when the brand is long ("Travel Agent · Business Class Tickets"): the line is never cut. */
#bbc-widget-root .bw-head__sub {
  margin-top: 1px; font-size: 13px; line-height: 1.35; color: var(--bw-muted);
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
#bbc-widget-root .bw-head__actions { display: flex; align-items: center; gap: 4px; }
#bbc-widget-root .bw-iconbtn {
  display: flex; align-items: center; justify-content: center;
  width: 44px; height: 44px; border-radius: 50%; color: var(--bw-text);
  transition: background-color 0.18s var(--bw-ease), color 0.18s var(--bw-ease);
}
#bbc-widget-root .bw-iconbtn:hover { background: rgba(var(--bw-primary-rgb, 30,79,216), 0.08); }
#bbc-widget-root .bw-iconbtn svg { width: 20px; height: 20px; }
#bbc-widget-root .bw-iconbtn--call { color: var(--bw-primary, var(--bw-ink)); background: var(--bw-glass); box-shadow: var(--bw-lift); }
#bbc-widget-root .bw-iconbtn--call svg { width: 18px; height: 18px; }
#bbc-widget-root .bw-iconbtn--call:hover { background: var(--bw-primary, var(--bw-ink)); color: #FFFFFF; }

#bbc-widget-root .bw-intro { padding: 6px 24px 18px; }
#bbc-widget-root .bw-intro__title {
  font-size: 26px; font-weight: 700; line-height: 1.22; letter-spacing: -0.02em;
  color: var(--bw-text); text-wrap: balance;
}
#bbc-widget-root .bw-intro__title em { font-style: normal; font-weight: 700; color: var(--bw-primary, var(--bw-accent)); }
#bbc-widget-root .bw-intro__lead { margin-top: 8px; font-size: 17px; line-height: 1.5; color: var(--bw-text-2); max-width: 34ch; }

/* ── The form ──────────────────────────────────────────────────────────
   Read by many clients over 70 (16 Nov): large type, tall fields with strong edges, the label above
   each field and what goes in it, the example under the field (never grey text inside it, which reads
   as already filled in), errors in words a person can see, and the number to call for who would rather
   talk. */
#bbc-widget-root .bw-form {
  flex: 1; overflow-y: auto; overscroll-behavior: contain;
  display: flex; flex-direction: column; gap: 20px; margin: 0 12px 12px; padding: 24px 20px 18px;
  border-radius: 20px; background: var(--bw-glass); box-shadow: var(--bw-lift);
}
/* The fields, then the button with the number to call: spaced like the form itself. */
#bbc-widget-root .bw-form__fields,
#bbc-widget-root .bw-form__foot { display: flex; flex-direction: column; gap: inherit; }
#bbc-widget-root .bw-field { display: flex; flex-direction: column; gap: 8px; }
#bbc-widget-root .bw-label { font-size: 17px; font-weight: 700; line-height: 1.3; color: var(--bw-text); }
#bbc-widget-root .bw-input {
  display: block; width: 100%; height: 56px; padding: 0 16px; border-radius: 14px;
  font-size: 18px; line-height: 1.3; color: var(--bw-text); caret-color: var(--bw-primary, var(--bw-ink));
  background: var(--bw-paper); border: 2px solid rgba(var(--bw-primary-rgb, 30,79,216), 0.32);
  -webkit-appearance: none; appearance: none;
  transition: border-color 0.15s var(--bw-ease), box-shadow 0.15s var(--bw-ease);
}
#bbc-widget-root .bw-input::placeholder { color: var(--bw-placeholder); opacity: 1; }
#bbc-widget-root .bw-input:hover { border-color: rgba(var(--bw-primary-rgb, 30,79,216), 0.6); }
#bbc-widget-root .bw-input:focus { border-color: var(--bw-primary, var(--bw-ink)); box-shadow: 0 0 0 4px rgba(var(--bw-primary-rgb, 30,79,216), 0.18); }
#bbc-widget-root .bw-input:focus-visible { outline: none; }
#bbc-widget-root .bw-input[aria-invalid="true"] { border-color: var(--bw-danger); box-shadow: 0 0 0 4px rgba(179, 38, 30, 0.14); }
/* A flagged field still shows where the keyboard is: the red ring looks the same focused or not, and a
   failed send moves the focus to the first flagged field. The ring of every other control, in red. */
#bbc-widget-root .bw-input[aria-invalid="true"]:focus-visible { outline: 2px solid var(--bw-danger); outline-offset: 2px; }
#bbc-widget-root .bw-phone {
  display: flex; align-items: stretch; height: 56px; border-radius: 14px; overflow: hidden;
  background: var(--bw-paper); border: 2px solid rgba(var(--bw-primary-rgb, 30,79,216), 0.32);
  transition: border-color 0.15s var(--bw-ease), box-shadow 0.15s var(--bw-ease);
}
#bbc-widget-root .bw-phone:hover { border-color: rgba(var(--bw-primary-rgb, 30,79,216), 0.6); }
#bbc-widget-root .bw-phone:focus-within { border-color: var(--bw-primary, var(--bw-ink)); box-shadow: 0 0 0 4px rgba(var(--bw-primary-rgb, 30,79,216), 0.18); }
#bbc-widget-root .bw-phone[data-invalid="true"] { border-color: var(--bw-danger); box-shadow: 0 0 0 4px rgba(179, 38, 30, 0.14); }
#bbc-widget-root .bw-phone[data-invalid="true"]:focus-within { outline: 2px solid var(--bw-danger); outline-offset: 2px; }
#bbc-widget-root .bw-phone__cc {
  display: flex; align-items: center; justify-content: center; min-width: 76px; padding: 0 14px;
  background: var(--bw-band); font-size: 16px; font-weight: 700; color: var(--bw-text-2);
  font-variant-numeric: tabular-nums; white-space: nowrap;
}
#bbc-widget-root .bw-phone .bw-input { flex: 1; height: 52px; border: 0; border-radius: 0; box-shadow: none; }
/* The number's box draws the ring for the field inside it: one ring, not two. */
#bbc-widget-root .bw-phone .bw-input:focus-visible { outline: none; }
#bbc-widget-root .bw-hint { font-size: 15px; line-height: 1.45; color: var(--bw-text-2); }
#bbc-widget-root .bw-error { display: flex; align-items: flex-start; gap: 8px; font-size: 16px; font-weight: 600; line-height: 1.4; color: var(--bw-danger); }
#bbc-widget-root .bw-error svg { width: 18px; height: 18px; margin-top: 2px; }
#bbc-widget-root .bw-cta {
  display: flex; align-items: center; justify-content: center; gap: 10px;
  width: 100%; height: 60px; margin-top: 4px; border-radius: 999px;
  background: var(--bw-primary, var(--bw-cta)); color: #FFFFFF;
  font-size: 19px; font-weight: 700; line-height: 1;
  box-shadow: 0 8px 20px rgba(var(--bw-primary-rgb, 30,79,216), 0.24);
  transition: background-color 0.18s var(--bw-ease);
}
#bbc-widget-root .bw-cta:hover { background: var(--bw-primary-hover, var(--bw-cta-hover)); }
#bbc-widget-root .bw-cta:active { transform: translateY(1px); }
#bbc-widget-root .bw-cta svg { width: 20px; height: 20px; transition: transform 0.2s var(--bw-ease); }
#bbc-widget-root .bw-cta:hover svg { transform: translateX(3px); }
#bbc-widget-root .bw-form__call {
  display: flex; align-items: center; justify-content: center; flex-wrap: wrap; column-gap: 6px;
  margin-top: -6px; font-size: 16px; line-height: 1.4; color: var(--bw-text-2); text-align: center;
}
#bbc-widget-root .bw-form__call a {
  display: inline-flex; align-items: center; gap: 6px; min-height: 44px; padding: 0 4px;
  font-weight: 700; color: var(--bw-primary, var(--bw-ink)); white-space: nowrap;
  text-decoration: underline; text-underline-offset: 4px; text-decoration-thickness: 1px;
}
#bbc-widget-root .bw-form__call a:hover { text-decoration-thickness: 2px; }
#bbc-widget-root .bw-form__call svg { width: 18px; height: 18px; }

/* ── The conversation ────────────────────────────────────────────────── */
#bbc-widget-root .bw-logwrap { position: relative; flex: 1; min-height: 0; display: flex; flex-direction: column; }
#bbc-widget-root .bw-log {
  flex: 1; overflow-y: auto; overscroll-behavior: contain;
  display: flex; flex-direction: column; padding: 8px 16px 12px;
  scrollbar-width: thin; scrollbar-color: var(--bw-line-strong) transparent;
}
/* Few messages sit at the bottom, by the buttons and the composer, never under an empty band (ADR 17). A
   flexible first item, not justify-content: flex-end, so a long conversation still scrolls to its top. */
#bbc-widget-root .bw-log::before { content: ''; flex: 1 1 auto; }
#bbc-widget-root .bw-log::-webkit-scrollbar { width: 6px; }
#bbc-widget-root .bw-log::-webkit-scrollbar-thumb { background: var(--bw-line-strong); border-radius: 3px; }
#bbc-widget-root .bw-row { display: flex; flex-direction: column; max-width: 86%; margin-top: 4px; animation: bw-msg-in 0.32s var(--bw-ease) both; }
#bbc-widget-root .bw-row--first { margin-top: 16px; }
#bbc-widget-root .bw-log > .bw-row:first-child { margin-top: 0; }
#bbc-widget-root .bw-row--in { align-self: flex-start; }
#bbc-widget-root .bw-row--out { align-self: flex-end; align-items: flex-end; }
#bbc-widget-root .bw-who { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: 12.5px; line-height: 1.3; color: var(--bw-muted); }
#bbc-widget-root .bw-who b { font-weight: 700; color: var(--bw-text); }
#bbc-widget-root .bw-ava { width: 26px; height: 26px; border-radius: 50%; object-fit: cover; background: var(--bw-band); flex-shrink: 0; }
#bbc-widget-root .bw-ava--brand { box-shadow: 0 0 0 1px var(--bw-line-strong); }
#bbc-widget-root .bw-ava--initials { display: flex; align-items: center; justify-content: center; background: var(--bw-text); color: #FFFFFF; font-size: 11px; font-weight: 700; }
#bbc-widget-root .bw-ava--persona { display: flex; align-items: center; justify-content: center; background: var(--bw-primary, var(--bw-ink)); color: #FFFFFF; font-size: 12px; font-weight: 700; }
/* Alex's photo fills his round avatar in all three spots; the brand colour stays behind it while it decodes. */
#bbc-widget-root .bw-photo > img { display: block; width: 100%; height: 100%; border-radius: 50%; object-fit: cover; }
#bbc-widget-root .bw-msg { padding: 10px 14px; font-size: 15px; line-height: 1.5; white-space: pre-wrap; overflow-wrap: anywhere; }
#bbc-widget-root .bw-row--in .bw-msg { background: var(--bw-glass); color: var(--bw-text); border-radius: 6px 18px 18px 18px; box-shadow: var(--bw-lift); }
#bbc-widget-root .bw-row--in:not(.bw-row--first) .bw-msg { border-radius: 18px; }
#bbc-widget-root .bw-row--out .bw-msg { background: var(--bw-primary, var(--bw-ink)); color: #FFFFFF; border-radius: 18px 18px 6px 18px; }
#bbc-widget-root .bw-msg a { color: var(--bw-primary, var(--bw-accent)); font-weight: 600; text-decoration: underline; text-underline-offset: 3px; text-decoration-thickness: 1px; }
#bbc-widget-root .bw-row--out .bw-msg a { color: #FFFFFF; }
#bbc-widget-root .bw-msg a[href^="tel:"] { white-space: nowrap; }
#bbc-widget-root .bw-meta { margin-top: 5px; font-size: 12px; line-height: 1.3; color: var(--bw-muted); font-variant-numeric: tabular-nums; }
#bbc-widget-root .bw-note { align-self: center; max-width: 92%; margin-top: 16px; text-align: center; font-size: 13px; line-height: 1.45; color: var(--bw-muted); animation: bw-msg-in 0.32s var(--bw-ease) both; }
#bbc-widget-root .bw-note--error { color: var(--bw-danger); }
#bbc-widget-root .bw-day { align-self: center; margin-top: 20px; padding: 3px 10px; border-radius: 999px; background: var(--bw-glass); font-size: 12px; font-weight: 600; line-height: 1.3; color: var(--bw-muted); }
#bbc-widget-root .bw-log > .bw-day:first-child { margin-top: 0; }
/* A message waiting to go: a light bubble with a dashed edge and the text at full strength (faded white
   on blue was 3:1, too faint to read; "Sends when the reply is in" says the rest). */
#bbc-widget-root .bw-row--queued .bw-msg {
  background: rgba(var(--bw-primary-rgb, 30,79,216), 0.08); color: var(--bw-text);
  outline: 1.5px dashed rgba(var(--bw-primary-rgb, 30,79,216), 0.55); outline-offset: -1.5px;
}
#bbc-widget-root .bw-retry { display: block; margin-left: auto; text-align: right; }
#bbc-widget-root .bw-retry u { text-decoration-thickness: 1px; text-underline-offset: 3px; }
#bbc-widget-root .bw-retry:hover u { text-decoration-thickness: 2px; }
#bbc-widget-root .bw-unseen {
  position: absolute; left: 50%; bottom: 12px; transform: translateX(-50%);
  display: flex; align-items: center; gap: 6px; height: 36px; padding: 0 16px; border-radius: 999px;
  background: var(--bw-primary, var(--bw-ink)); color: #FFFFFF; font-size: 13px; font-weight: 600; white-space: nowrap;
  box-shadow: 0 12px 32px rgba(16, 24, 40, 0.22);
  animation: bw-unseen-in 0.28s var(--bw-ease) both;
}
#bbc-widget-root .bw-unseen svg { width: 15px; height: 15px; }
#bbc-widget-root .bw-note a { font-weight: 600; text-decoration: underline; text-underline-offset: 3px; text-decoration-thickness: 1px; white-space: nowrap; }
#bbc-widget-root .bw-meta--failed { color: var(--bw-danger); }
#bbc-widget-root .bw-dots { display: inline-flex; align-items: center; gap: 5px; padding: 14px 16px; border-radius: 6px 18px 18px 18px; background: var(--bw-glass); box-shadow: var(--bw-lift); }
#bbc-widget-root .bw-dots i { width: 6px; height: 6px; border-radius: 50%; background: var(--bw-muted); animation: bw-dot 1.2s infinite ease-in-out; }
#bbc-widget-root .bw-dots i:nth-child(2) { animation-delay: 0.15s; }
#bbc-widget-root .bw-dots i:nth-child(3) { animation-delay: 0.3s; }

/* The replies offered under the last message: pills, as in the canvas. */
#bbc-widget-root .bw-chips { flex-shrink: 0; display: flex; flex-wrap: wrap; gap: 8px; padding: 2px 16px 12px; }
#bbc-widget-root .bw-chip {
  min-height: 40px; padding: 8px 16px; border-radius: 999px;
  border: 1px solid rgba(var(--bw-primary-rgb, 30,79,216), 0.3);
  background: rgba(255, 255, 255, 0.92); color: var(--bw-primary, var(--bw-ink));
  font-size: 14px; font-weight: 600; line-height: 1.3; text-align: left;
  transition: background-color 0.15s var(--bw-ease), color 0.15s var(--bw-ease), border-color 0.15s var(--bw-ease);
}
#bbc-widget-root .bw-chip:hover { background: var(--bw-primary, var(--bw-ink)); border-color: var(--bw-primary, var(--bw-ink)); color: #FFFFFF; }
#bbc-widget-root .bw-chip--channel { display: inline-flex; align-items: center; gap: 8px; }
#bbc-widget-root .bw-chip--channel svg { width: 17px; height: 17px; flex-shrink: 0; }

/* Where the agent writes (channel.ts): one field, the country beside a number, then send. */
#bbc-widget-root .bw-channel {
  flex-shrink: 0; display: flex; flex-direction: column; gap: 8px;
  margin: 0 12px 12px; padding: 14px; border-radius: 20px;
  background: rgba(255, 255, 255, 0.96); border: 1px solid rgba(var(--bw-primary-rgb, 30,79,216), 0.16);
  box-shadow: 0 2px 10px rgba(16, 24, 40, 0.06);
  animation: bw-msg-in 0.28s var(--bw-ease) both;
}
#bbc-widget-root .bw-channel__label { display: flex; align-items: center; gap: 8px; font-size: 14px; font-weight: 700; color: var(--bw-text); }
#bbc-widget-root .bw-channel__label svg { width: 18px; height: 18px; color: var(--bw-primary, var(--bw-ink)); }
#bbc-widget-root .bw-channel__row {
  display: flex; align-items: stretch; border-radius: 14px; overflow: hidden;
  border: 1px solid rgba(var(--bw-primary-rgb, 30,79,216), 0.28); background: #FFFFFF;
}
#bbc-widget-root .bw-channel__row:focus-within { border-color: rgba(var(--bw-primary-rgb, 30,79,216), 0.6); box-shadow: 0 0 0 3px rgba(var(--bw-primary-rgb, 30,79,216), 0.12); }
#bbc-widget-root .bw-channel__row--error { border-color: var(--bw-danger); }
#bbc-widget-root .bw-channel__cc {
  flex-shrink: 0; max-width: 112px; padding: 0 8px; border: 0; border-right: 1px solid var(--bw-line);
  background: rgba(var(--bw-primary-rgb, 30,79,216), 0.05); color: var(--bw-text); font-size: 15px;
}
#bbc-widget-root .bw-channel__input, #bbc-widget-root .bw-channel__price {
  flex: 1; min-width: 0; height: 46px; padding: 0 12px; border: 0; background: transparent;
  color: var(--bw-text); font-size: 16px;
}
#bbc-widget-root .bw-channel__input:focus, #bbc-widget-root .bw-channel__price:focus { outline: none; box-shadow: none; }
#bbc-widget-root .bw-channel__price { border: 1px solid var(--bw-line); border-radius: 14px; background: #FFFFFF; }
#bbc-widget-root .bw-channel__hint { font-size: 12.5px; line-height: 1.4; color: var(--bw-muted); }
#bbc-widget-root .bw-channel__error { font-size: 13px; line-height: 1.4; color: var(--bw-danger); }
#bbc-widget-root .bw-channel__actions { display: flex; justify-content: flex-end; gap: 8px; }
#bbc-widget-root .bw-channel__back { min-height: 44px; padding: 0 14px; border-radius: 999px; color: var(--bw-muted); font-size: 14px; font-weight: 600; }
#bbc-widget-root .bw-channel__back:hover { color: var(--bw-text); }
#bbc-widget-root .bw-channel__send {
  min-height: 44px; padding: 0 22px; border-radius: 999px;
  background: var(--bw-primary, var(--bw-ink)); color: #FFFFFF; font-size: 15px; font-weight: 600;
}
#bbc-widget-root .bw-channel__send:hover { background: var(--bw-primary-hover, #203359); }
#bbc-widget-root .bw-compose.bw-compose--hidden { display: none; }

/* The composer: a rounded field with a round send button inside it. */
#bbc-widget-root .bw-compose {
  flex-shrink: 0; display: flex; align-items: flex-end; gap: 8px;
  margin: 0 12px 12px; padding: 4px 4px 4px 14px; border-radius: 24px;
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid rgba(var(--bw-primary-rgb, 30,79,216), 0.16);
  box-shadow: 0 2px 10px rgba(16, 24, 40, 0.06);
  transition: border-color 0.15s var(--bw-ease), box-shadow 0.15s var(--bw-ease);
}
#bbc-widget-root .bw-compose:focus-within { border-color: rgba(var(--bw-primary-rgb, 30,79,216), 0.55); box-shadow: 0 0 0 3px rgba(var(--bw-primary-rgb, 30,79,216), 0.12); }
#bbc-widget-root .bw-compose .bw-input {
  flex: 1; height: 44px; min-height: 44px; max-height: 136px; padding: 11px 0;
  border: 0; border-radius: 0; background: transparent; box-shadow: none;
  font-size: 16px; line-height: 1.4; resize: none; overflow-y: auto;
}
#bbc-widget-root .bw-compose .bw-input:focus { box-shadow: none; }
#bbc-widget-root .bw-send {
  display: flex; align-items: center; justify-content: center;
  width: 40px; height: 40px; margin: 2px; flex-shrink: 0; border-radius: 50%;
  background: var(--bw-primary, var(--bw-ink)); color: #FFFFFF;
  transition: background-color 0.18s var(--bw-ease), opacity 0.18s var(--bw-ease), transform 0.12s var(--bw-ease);
}
#bbc-widget-root .bw-send:hover:not(:disabled) { background: var(--bw-primary-hover, #203359); }
#bbc-widget-root .bw-send:active:not(:disabled) { transform: scale(0.94); }
#bbc-widget-root .bw-send:disabled { opacity: 0.35; cursor: default; }
#bbc-widget-root .bw-send svg { width: 18px; height: 18px; }

/* ── Phones: a full-screen sheet ─────────────────────────────────────── */
@media (max-width: 767px) {
  #bbc-widget-root .bw-panel,
  #bbc-widget-root .bw-panel--form {
    top: var(--bw-vvt, 0px); left: 0; right: 0; bottom: auto; width: 100%; max-width: none;
    height: 100%; height: var(--bw-vvh, 100dvh); max-height: none; border-radius: 0;
    box-shadow: none; animation-name: bw-sheet-in; animation-duration: 0.34s;
  }
  #bbc-widget-root .bw-head { padding-top: max(14px, env(safe-area-inset-top)); }
  #bbc-widget-root .bw-intro { padding: 6px 20px 18px; }
  #bbc-widget-root .bw-intro__title { font-size: clamp(23px, 7vw, 28px); }
  /* The form screen: the title on top, the form under it near the thumb. On a short phone the whole
     sheet scrolls, so the button is never cut in half. */
  #bbc-widget-root .bw-panel--form { overflow-y: auto; overscroll-behavior: contain; }
  #bbc-widget-root .bw-panel--form .bw-top { flex: 1 0 auto; display: flex; flex-direction: column; justify-content: space-between; }
  #bbc-widget-root .bw-panel--form .bw-form { flex: 0 0 auto; overflow: visible; margin-bottom: max(12px, env(safe-area-inset-bottom)); }
  #bbc-widget-root .bw-compose { margin-bottom: max(12px, env(safe-area-inset-bottom)); }
  #bbc-widget-root .bw-msg { font-size: 16px; }
  #bbc-widget-root .bw-chip { min-height: 44px; font-size: 15px; }
  #bbc-widget-root .bw-nudge { right: 0; bottom: calc(100% + 12px); width: min(300px, calc(100vw - 32px)); }
}
/* Most phones show 560–760 px of page: the button (and on most, the number to call) on the first screen,
   with the keyboard closed. The space around the fields shrinks and the line under the title goes; the
   fields, their type and the button keep their size. With the keyboard open the sheet still scrolls. */
@media (max-width: 767px) and (max-height: 760px) {
  #bbc-widget-root .bw-panel--form .bw-head { min-height: 60px; padding-bottom: 6px; }
  #bbc-widget-root .bw-panel--form .bw-intro { padding: 0 20px 12px; }
  #bbc-widget-root .bw-panel--form .bw-intro__title { font-size: clamp(22px, 6.4vw, 25px); }
  #bbc-widget-root .bw-panel--form .bw-intro__lead { display: none; }
  #bbc-widget-root .bw-panel--form .bw-form { gap: 14px; padding-top: 18px; padding-bottom: 14px; }
  #bbc-widget-root .bw-panel--form .bw-field { gap: 6px; }
}
@media (max-width: 767px) and (max-height: 620px) {
  #bbc-widget-root .bw-head { min-height: 60px; padding-top: 8px; padding-bottom: 8px; }
  #bbc-widget-root .bw-intro { padding-bottom: 12px; }
  #bbc-widget-root .bw-panel--form .bw-form { gap: 12px; padding-top: 14px; padding-bottom: 14px; }
}
#bbc-widget-root .bw-panel--embedded { animation: none; }

/* ── Computers: the form's button never leaves the screen ──────────────
   A 1366×768 laptop leaves the page some 620–660 px of height once the browser and the taskbar take
   theirs, and many older clients zoom to 125% or more. The fields scroll inside the card when they must;
   the button and the number to call stay under them, in sight. */
@media (min-width: 768px) {
  /* E2 (keyboard.ts): while the on-screen keyboard is open over a floating panel (a tablet, a phone held
     sideways), the panel rises above it and stays no taller than what is still visible. The phone sheet
     follows the visible area itself; without a keyboard nothing here applies. */
  #bbc-widget-root.bw-kb-open .bw-panel:not(.bw-panel--embedded) {
    bottom: calc(var(--bw-bottom) + var(--bw-kb, 0px));
    max-height: calc(var(--bw-avail, 100dvh) - 32px);
  }
  #bbc-widget-root .bw-panel--form .bw-form { overflow: hidden; }
  #bbc-widget-root .bw-panel--form .bw-form__fields {
    flex: 0 1 auto; min-height: 0; overflow-y: auto; overscroll-behavior: contain;
    margin: -4px; padding: 4px; /* room for the focus ring inside the scrolling part */
  }
  /* A field brought into view by the keyboard comes with its label above it and its whole ring. */
  #bbc-widget-root .bw-panel--form .bw-form__fields .bw-input { scroll-margin: 36px 0 10px; }
  #bbc-widget-root .bw-panel--form .bw-form__foot { flex-shrink: 0; }
}

/* ── The whole screen, with bigger text (computers; largeView.ts) ──────────
   Asked for the clients over 70 who read the corner panel with difficulty (16 Nov). The panel covers the
   page; the header, the conversation and the composer sit in one column a person can read; every size
   goes up. Phones are full-screen sheets already, so none of this applies under 768px. */
@media (min-width: 768px) {
  #bbc-widget-root .bw-panel.bw-panel--large {
    top: 0; left: 0; right: 0; bottom: 0; z-index: 2147483001;
    width: 100%; max-width: none; height: 100%; max-height: none;
    border-radius: 0; box-shadow: none; animation: bw-fade 0.22s var(--bw-ease) both;
  }
  #bbc-widget-root .bw-panel--large .bw-top,
  #bbc-widget-root .bw-panel--large .bw-logwrap,
  #bbc-widget-root .bw-panel--large .bw-chips { width: 100%; max-width: 880px; margin-left: auto; margin-right: auto; }
  #bbc-widget-root .bw-panel--large .bw-head { min-height: 88px; gap: 16px; padding: 20px 12px 12px 20px; }
  #bbc-widget-root .bw-panel--large .bw-head__ava { width: 52px; height: 52px; font-size: 21px; }
  #bbc-widget-root .bw-panel--large .bw-head__name { font-size: 21px; }
  #bbc-widget-root .bw-panel--large .bw-head__sub { font-size: 16px; }
  #bbc-widget-root .bw-panel--large .bw-iconbtn { width: 52px; height: 52px; }
  #bbc-widget-root .bw-panel--large .bw-iconbtn svg { width: 24px; height: 24px; }
  #bbc-widget-root .bw-panel--large .bw-log { padding: 12px 20px 16px; }
  #bbc-widget-root .bw-panel--large .bw-row { max-width: 80%; margin-top: 6px; }
  #bbc-widget-root .bw-panel--large .bw-row--first { margin-top: 20px; }
  #bbc-widget-root .bw-panel--large .bw-msg { padding: 14px 18px; font-size: 20px; line-height: 1.55; }
  #bbc-widget-root .bw-panel--large .bw-who { gap: 10px; font-size: 16px; }
  #bbc-widget-root .bw-panel--large .bw-ava { width: 32px; height: 32px; }
  #bbc-widget-root .bw-panel--large .bw-ava--initials,
  #bbc-widget-root .bw-panel--large .bw-ava--persona { font-size: 14px; }
  #bbc-widget-root .bw-panel--large .bw-meta { font-size: 14px; }
  #bbc-widget-root .bw-panel--large .bw-note { font-size: 16px; }
  #bbc-widget-root .bw-panel--large .bw-day { padding: 4px 12px; font-size: 14px; }
  #bbc-widget-root .bw-panel--large .bw-dots { padding: 18px 20px; }
  #bbc-widget-root .bw-panel--large .bw-dots i { width: 8px; height: 8px; }
  #bbc-widget-root .bw-panel--large .bw-unseen { height: 44px; padding: 0 20px; font-size: 16px; }
  #bbc-widget-root .bw-panel--large .bw-chips { gap: 10px; padding: 4px 20px 16px; }
  #bbc-widget-root .bw-panel--large .bw-chip { min-height: 54px; padding: 12px 22px; font-size: 18px; }
  #bbc-widget-root .bw-panel--large .bw-compose {
    width: calc(100% - 24px); max-width: 856px; margin: 0 auto 24px; padding: 6px 6px 6px 20px; border-radius: 32px;
  }
  #bbc-widget-root .bw-panel--large .bw-compose .bw-input { height: 60px; min-height: 60px; max-height: 180px; padding: 16px 0; font-size: 20px; }
  #bbc-widget-root .bw-panel--large .bw-send { width: 52px; height: 52px; margin: 4px; }
  #bbc-widget-root .bw-panel--large .bw-send svg { width: 24px; height: 24px; }
  /* The form, big: in the middle of the page, in a column of its own; on a short screen the card stops
     at the screen's bottom with the button in it, and the fields scroll inside the card. */
  #bbc-widget-root .bw-panel--large.bw-panel--form { overflow-y: auto; }
  #bbc-widget-root .bw-panel--large.bw-panel--form .bw-top { max-width: 624px; }
  #bbc-widget-root .bw-panel--large .bw-intro { padding: 12px 24px 24px; }
  #bbc-widget-root .bw-panel--large .bw-intro__title { font-size: 34px; }
  #bbc-widget-root .bw-panel--large .bw-intro__lead { font-size: 20px; max-width: 40ch; }
  #bbc-widget-root .bw-panel--large .bw-form {
    flex: 0 1 auto; min-height: 0; overflow: hidden; width: calc(100% - 24px); max-width: 600px;
    margin: 0 auto 32px; padding: 28px 24px 22px; gap: 24px;
  }
  #bbc-widget-root .bw-panel--large .bw-label { font-size: 20px; }
  #bbc-widget-root .bw-panel--large .bw-input { height: 62px; font-size: 21px; }
  #bbc-widget-root .bw-panel--large .bw-phone { height: 62px; }
  #bbc-widget-root .bw-panel--large .bw-phone .bw-input { height: 58px; }
  #bbc-widget-root .bw-panel--large .bw-phone__cc { min-width: 88px; font-size: 18px; }
  #bbc-widget-root .bw-panel--large .bw-hint { font-size: 17px; }
  #bbc-widget-root .bw-panel--large .bw-error { font-size: 18px; }
  #bbc-widget-root .bw-panel--large .bw-error svg { width: 20px; height: 20px; }
  #bbc-widget-root .bw-panel--large .bw-cta { height: 66px; font-size: 21px; }
  #bbc-widget-root .bw-panel--large .bw-form__call { font-size: 18px; }
}
/* A short computer screen: the space around the fields shrinks first, then the line under the title
   goes. The fields, their type and the button keep their size. */
@media (min-width: 768px) and (max-height: 840px) {
  #bbc-widget-root .bw-panel--form:not(.bw-panel--large) .bw-head { min-height: 64px; padding-top: 10px; padding-bottom: 6px; }
  #bbc-widget-root .bw-panel--form:not(.bw-panel--large) .bw-intro { padding: 0 24px 12px; }
  #bbc-widget-root .bw-panel--form:not(.bw-panel--large) .bw-intro__title { font-size: 24px; }
  #bbc-widget-root .bw-panel--form:not(.bw-panel--large) .bw-intro__lead { margin-top: 4px; font-size: 16px; }
  #bbc-widget-root .bw-panel--form:not(.bw-panel--large) .bw-form { gap: 16px; padding: 18px 20px 14px; }
  #bbc-widget-root .bw-panel--form:not(.bw-panel--large) .bw-field { gap: 6px; }
  #bbc-widget-root .bw-panel--large.bw-panel--form .bw-head { min-height: 72px; padding-top: 12px; padding-bottom: 8px; }
  #bbc-widget-root .bw-panel--large .bw-intro { padding: 0 24px 16px; }
  #bbc-widget-root .bw-panel--large .bw-form { gap: 18px; padding: 22px 24px 16px; margin-bottom: 16px; }
}
@media (min-width: 768px) and (max-height: 760px) {
  #bbc-widget-root .bw-panel--form .bw-intro__lead { display: none; }
  #bbc-widget-root .bw-panel--form .bw-form__call a { min-height: 36px; }
  #bbc-widget-root .bw-panel--form:not(.bw-panel--large) .bw-form { gap: 14px; padding-top: 16px; }
  #bbc-widget-root .bw-panel--large.bw-panel--form .bw-head { min-height: 64px; padding-top: 8px; padding-bottom: 4px; }
  #bbc-widget-root .bw-panel--large .bw-intro { padding-bottom: 12px; }
  #bbc-widget-root .bw-panel--large .bw-form { gap: 14px; padding-top: 16px; padding-bottom: 12px; margin-bottom: 12px; }
  #bbc-widget-root .bw-panel--large .bw-field { gap: 6px; }
}
@media (min-width: 768px) and (max-height: 640px) {
  #bbc-widget-root .bw-panel--form:not(.bw-panel--large) .bw-head { padding-top: 6px; padding-bottom: 4px; }
  #bbc-widget-root .bw-panel--form:not(.bw-panel--large) .bw-intro { padding-bottom: 8px; }
  #bbc-widget-root .bw-panel--form:not(.bw-panel--large) .bw-intro__title { font-size: 22px; }
  #bbc-widget-root .bw-panel--form:not(.bw-panel--large) .bw-form { gap: 12px; padding-top: 12px; padding-bottom: 10px; }
  #bbc-widget-root .bw-panel--form:not(.bw-panel--large) .bw-form__call a { min-height: 30px; }
}
/* Phones: already full-screen; the button that makes the panel bigger is not drawn. */
@media (max-width: 767px) {
  #bbc-widget-root .bw-iconbtn--size { display: none; }
}

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
@keyframes bw-pop { from { transform: scale(0.4); opacity: 0; } to { transform: none; opacity: 1; } }
@keyframes bw-pulse { from { transform: scale(1); opacity: 0.9; } to { transform: scale(1.55); opacity: 0; } }
@keyframes bw-rise { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
@keyframes bw-msg-in { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
@keyframes bw-dot { 0%, 80%, 100% { opacity: 0.3; } 40% { opacity: 1; } }

@media (prefers-reduced-motion: reduce) {
  #bbc-widget-root *, #bbc-widget-root *::before, #bbc-widget-root *::after { animation: none !important; transition: none !important; }
}

/* D1 (FormRow.tsx): the row beside the site's refused quote form, outside the widget's root — its own
   reset, so the site's button styles do not reach it, and the brand's colour from its host. */
#bbc-form-row { display: block; margin-top: 12px; margin-bottom: 4px; font-family: inherit; }
#bbc-form-row .bw-row-offer {
  position: relative; display: flex; flex-direction: column; gap: 10px; box-sizing: border-box;
  padding: 14px 44px 14px 16px; border-radius: 14px; text-align: left;
  background: #FFFFFF; border: 1px solid rgba(var(--bw-primary-rgb, 30,79,216), 0.35);
  box-shadow: 0 4px 14px rgba(16, 24, 40, 0.08);
  color: #1F2937; font-size: 15px; line-height: 1.45;
}
#bbc-form-row .bw-row-offer__text { margin: 0; font: inherit; color: inherit; }
#bbc-form-row .bw-row-offer__actions { display: flex; flex-wrap: wrap; gap: 8px; }
#bbc-form-row button { all: unset; box-sizing: border-box; cursor: pointer; font-family: inherit; }
#bbc-form-row .bw-row-offer__btn {
  display: inline-flex; align-items: center; gap: 8px; min-height: 44px; padding: 0 16px; border-radius: 999px;
  background: var(--bw-primary, #1E4FD8); color: #FFFFFF; font-size: 15px; font-weight: 600; line-height: 1.2;
}
#bbc-form-row .bw-row-offer__btn:hover { background: var(--bw-primary-hover, #203359); }
#bbc-form-row .bw-row-offer__btn:focus-visible, #bbc-form-row .bw-row-offer__x:focus-visible {
  outline: 2px solid var(--bw-primary, #1E4FD8); outline-offset: 2px;
}
#bbc-form-row .bw-row-offer__btn svg, #bbc-form-row .bw-row-offer__x svg {
  width: 17px; height: 17px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round;
}
#bbc-form-row .bw-row-offer__x {
  position: absolute; top: 6px; right: 6px; display: flex; align-items: center; justify-content: center;
  width: 36px; height: 36px; border-radius: 50%; color: #6B7280;
}
#bbc-form-row .bw-row-offer__x:hover { color: #111827; background: rgba(0, 0, 0, 0.05); }
`
