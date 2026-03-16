---
description: Debate an implementation approach — two perspectives argue, then a verdict is given
---

# /debate — Implementation Debate

You are tasked with debating an implementation decision for the BBC Admin Panel project.

## Process

1. **Understand the question**: The user will describe a feature or technical decision.

2. **Advocate A** — argue FOR the first approach. Give concrete code examples, cite project conventions from `CLAUDE.md`, and list benefits.

3. **Advocate B** — argue AGAINST Advocate A's approach and propose an alternative. Give concrete code examples, cite trade-offs, and explain risks.

4. **Verdict** — weigh both sides fairly and give a final recommendation. Consider:
   - Consistency with existing codebase patterns
   - Maintainability and readability
   - Performance implications
   - Time to implement
   - Alignment with `specs/` if a spec exists

## Output Format

```
## 🅰️ Advocate A: <approach name>

<argument with code examples>

**Pros:** ...
**Cons:** ...

---

## 🅱️ Advocate B: <approach name>

<argument with code examples>

**Pros:** ...
**Cons:** ...

---

## ⚖️ Verdict

**Winner:** <A or B>
**Reasoning:** <2-3 sentences>
**Action:** <specific next step>
```

## Rules

- Both advocates must reference actual project files and patterns.
- No strawman arguments — both sides must be genuinely viable.
- The verdict must be actionable, not wishy-washy.
- If the decision is covered by a convention in `CLAUDE.md`, say so immediately — no need to debate settled conventions.

$ARGUMENTS
