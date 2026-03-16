---
name: bbc-copilot-prompt
description: Generate mega-prompts for Nasalciuc. Use when user says 'prompt for Nasalciuc' or 'copilot prompt'. Exact FIND/REPLACE blocks.
---

Format: TASK → CONTEXT → PRE-FLIGHT (git) → CHANGES (FIND/REPLACE per file ~lines) → POST-FLIGHT (build, commit) → VERIFICATION
Rules: English only. Exact code. DELETE→CREATE→MODIFY. One branch per task. feat:/fix:/chore:
