---
target: "https://chat.buybusinessclass.com/ (deep pass: detail, users, teams, KB, settings, dark mode)"
total_score: 20
max_score: 40
na_heuristics: 
p0_count: 1
p1_count: 4
timestamp: 2026-08-10T07-59-03Z
slug: chat-buybusinessclass-com
---
Method: dual-agent (A: design-review agent · B: detector-evidence agent)

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 2 | Every chat switch = bare "Loading..." (staleTime:0); send/claim/close give zero feedback |
| 2 | Match System / Real World | 2 | "Tunnel" jargon, model names ("sonnet") on client-facing bubbles, mixed date formats |
| 3 | User Control and Freedom | 2 | KB modal: no Esc/outside-click; close dialog mute on API failure |
| 4 | Consistency and Standards | 1 | Native confirm() vs ConfirmDialog; two "Active" badge vocabularies; per-page header roulette |
| 5 | Error Prevention | 3 | Create-Lead state machine + block-dialog copy are genuinely good |
| 6 | Recognition Rather Than Recall | 2 | All 1292 closed rows titled "Closed conversation" — names erased, no privacy gained |
| 7 | Flexibility and Efficiency | 2 | Enter-to-send is the only shortcut; no KB search; ⌘K hint on Windows |
| 8 | Aesthetic and Minimalist Design | 3 | Sidebar cards well-chunked; 4-color status strip noisy |
| 9 | Error Recovery | 1 | `catch { /* silently */ }` on send/claim/close; KB save try/finally with no catch |
| 10 | Help and Documentation | 2 | Good inline hints; everything else hover-only |
| **Total** | | **20/40** | **Acceptable — low end** |

## Design Specificity Verdict

**LLM assessment:** Two products in one shell. Operator surfaces (chats, detail, KB) are authored for a premium travel-sales operation — chat numbers, tunnels, lead tiers, live typing preview, an "earned red" presence philosophy in code comments. Users/Teams/Settings/auth are unmodified shadcn-admin template: "Social emails — friend requests, follows" in an internal ops tool, ToS links to routes that don't exist. The brand system is schizophrenic: theme.css defines complete oklch light/dark tokens incl. --bbc-gold/--bbc-navy, then ~194 hard-coded color literals across 28 files bypass them.

**Deterministic scan:** 8 findings (6 admin + 2 widget): side-tab border-l-4 ×4 (kpi-cards.tsx:86/120/156/209, VERIFIED), animate-bounce typing dots (detail.tsx:496-498, VERIFIED), width transition (lead-funnel.tsx:86, VERIFIED — already fixed on PR #177), widget bbc-bounce (VERIFIED — already fixed on PR #177), widget borderLeft tooltip arrow (FALSE POSITIVE — CSS triangle). Sweeps: 73 brand-hex lines in 16 files; 33 light-only Tailwind color lines without dark: variants; 34 inline styles (20 by design in widget-embed).

**Visual overlays:** injection preflight succeeded on the live page, but the overlay was skipped — the localhost-served detector script cannot be injected into the remote-origin HTTPS prod page (mixed content). No overlay is claimed.

## Overall Impression

The operator core is real product work sitting on an unfinished chassis. The deepest problem isn't visual polish — it's that the trust-critical paths (send, claim, close, KB save) fail silently, and the layout sacrifices the lead panel (the money context) first. Biggest single opportunity: make failure loud and the lead panel survivable below 1280px.

## What's Working

1. Typing preview with live draft text (detail.tsx:489-507) — a genuine sales superpower, the most authored moment in the product.
2. Honest presence philosophy (presence.ts) — "red must be EARNED", grace windows, documented in code.
3. Create-Lead state machine + moderation copy (detail.tsx:577-643, :683) — human-readable gating, exemplary trust copy.
4. Token infrastructure (theme.css) — the complete dark-capable palette already exists; the debt is that nothing uses it.

## Priority Issues

1. **[P0] Silent failure on send / claim / close** — detail.tsx:231-233, 252-254, 263-265. An agent's reply to a live customer can vanish with zero signal; claim races fail mutely. Fix: toast.error + inline retry on composer, claim-conflict surface, error text in the close dialog. → `/impeccable harden`
2. **[P1] Lead panel amputated <1280px, no affordance** — detail.tsx:721 (`hidden xl:block`), confirmed live at 1242px. The product's reason-to-exist disappears on ordinary laptops. Fix: collapsible panel/sheet with a header chip. → `/impeccable adapt`
3. **[P1] Dark mode structurally broken** — ~194 color literals in 28 files vs unused dark-ready tokens; worst offenders detail.tsx TIER_COLORS/typing card, KB TUNNEL_STYLES. Fix: migrate to tokens + semantic status tints. → `/impeccable colorize`
4. **[P1] "Closed conversation" erases visitor names** — detail.tsx:392-393 + list; 1292 identical rows while phone/email print 20px lower. Fix: keep names; closed lives in status chips. → `/impeccable clarify`
5. **[P1] Sub-AA text on navy header** — muted-on-navy ≈3.9:1 at 10-12px (detail.tsx:413-446); KB stale badge 3.1:1. Fix: on-navy muted token (white/70) + 11px floor. → `/impeccable typeset`
6. **[P2] Interaction-pattern debt** — native confirm() vs ConfirmDialog, hand-rolled modal without role=dialog/focus-trap, two badge vocabularies, ReadyToggle+Bell exist only on /chats (operator goes blind in KB). Fix: one Dialog, one badge vocabulary, one global header. → `/impeccable polish`
7. **[P2] KB failure & findability** — save try/finally without catch (silent unsaved close), no search over AI-critical articles, "0 uses" noise. → `/impeccable harden`
8. **[P2] Template residue in production** — "friend requests, follows" notifications, "how others will see you on the site", dead /terms /privacy links, debug JSON toast on settings submit. → `/impeccable distill`
9. **[P2] KPI side-tab borders ×4** — kpi-cards.tsx (detector, verified) — the #1 AI-generated tell, on the owner's first screen. → `/impeccable polish`

## Persona Red Flags

**Alex (power):** zero keyboard paths (no Esc, no j/k, no claim shortcut); staleTime:0 forces visible reload on every chat click; no KB search; ⌘K on Windows.
**Sam (a11y):** conversation rows are buttons with NO accessible name (live tree verified); icon-only unlabeled buttons (close X, copy-ID, KB edit/delete); disabled-reason only in title attrs; bare-div modal (no role/focus-trap/Esc); color-only 10px state dots.
**Maria (operator, 8h/day):** the P0 lands on her mid-conversation; Ready toggle + bell vanish outside /chats; lead panel gone at her window width; smallest text where she reads most; no date separators — a 3-day thread reads as one session.

## Minor Observations

Users table renders empty-state while loading (no skeleton) · test data live in prod ("Test group" team, "TEST AUTO-EMBED" KB article the AI consumes) · route transitions flash white (no pending UI) · lead-score 50-79 renders gray = looks disabled · "Blocked: nothing" possible success copy · KB 📋 emoji among lucide icons · Settings double navigation · "Invite User" + "Add User" undifferentiated · BBCAvatar neon initials vs "never neon" brand rule · KB typos feed the AI ("available available").

## Questions to Consider

1. The lead panel is the money — why is it the first thing the layout sacrifices instead of the last?
2. Maria closes dozens of conversations daily to zero feedback — what would "#1285 closed · 3 leads created today" cost? One toast.
3. presence.ts proves the team can design honesty. What does that philosophy demand of `catch { /* send failed silently */ }`?
4. Half the product is authored, half is template. Which half do new hires believe is the real company?
