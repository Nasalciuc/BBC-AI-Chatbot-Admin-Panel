---
name: bbc-copilot-prompt
description: Generate Copilot mega-prompts for Nasalciuc. Use when user says 'prompt for Nasalciuc', 'copilot prompt', or 'generate task'. Outputs exact FIND/REPLACE blocks.
---

# Format (mandatory)
TASK → CONTEXT (why) → PRE-FLIGHT (git checkout) → CHANGES (exact FIND/REPLACE per file with ~line numbers) → POST-FLIGHT (build, lint, commit) → VERIFICATION (what UI looks like)

# Rules
ALWAYS English. FIND = exact code with ~line numbers. Order: DELETE→CREATE→MODIFY. Full imports. One branch per task. Commit: feat:/fix:/chore:
