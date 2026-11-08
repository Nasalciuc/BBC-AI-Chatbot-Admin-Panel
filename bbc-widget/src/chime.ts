// A soft two-note chime for a message that arrives while the chat is closed or the tab is in the
// background. Drawn with Web Audio (no file to load). Browsers only let a page make sound after the
// visitor has interacted with it, so the audio context is unlocked on their first tap, click or key
// press on the page; until then the chime stays silent and the badge alone does the job.

type AC = AudioContext
let ctx: AC | null = null
let armed = false

function unlock() {
  try {
    const Ctor = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    if (!Ctor) return
    if (!ctx) ctx = new Ctor()
    if (ctx.state === 'suspended') ctx.resume().catch(() => { /* still locked — try on the next gesture */ })
    if (ctx.state === 'running') disarm()
  } catch { /* no Web Audio — the badge carries the news */ }
}

const EVENTS = ['pointerdown', 'keydown', 'touchstart'] as const
function disarm() { EVENTS.forEach(ev => window.removeEventListener(ev, unlock, true)) }

/** Listen for the visitor's first gesture so the chime may play later. Idempotent. */
export function armChime() {
  if (armed) return
  armed = true
  EVENTS.forEach(ev => window.addEventListener(ev, unlock, { capture: true, passive: true }))
}

function note(c: AC, freq: number, at: number, peak: number) {
  const osc = c.createOscillator()
  const gain = c.createGain()
  osc.type = 'sine'
  osc.frequency.value = freq
  gain.gain.setValueAtTime(0.0001, at)
  gain.gain.exponentialRampToValueAtTime(peak, at + 0.02)
  gain.gain.exponentialRampToValueAtTime(0.0001, at + 0.7)
  osc.connect(gain)
  gain.connect(c.destination)
  osc.start(at)
  osc.stop(at + 0.75)
}

/** Play the chime if the page may make sound; otherwise do nothing. */
export function playChime() {
  try {
    if (!ctx || ctx.state !== 'running') return
    const t = ctx.currentTime + 0.01
    note(ctx, 987.77, t, 0.09)        // B5
    note(ctx, 1318.51, t + 0.13, 0.07) // E6
  } catch { /* never let a sound break the chat */ }
}
