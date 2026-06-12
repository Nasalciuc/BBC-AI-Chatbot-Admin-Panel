# Architecture Decision Records

## ADR-10 — Scheduler-ul aplicației bate intern; Actions = redundanță

**Context:** GitHub throttle-uiește scheduled workflows pe repo-uri cu activitate redusă — `*/5` măsurat la 1-2h real (12 iun). Pe această cadență stăteau abandoned-crm ȘI sweep-ul deadline-ului de 8 min (#101).

**Decizie:** task asyncio în startup rulează ambele joburi la 300s, prin apel direct la logică; endpoint-urile HTTP + Actions rămân ca backup best-effort (joburi idempotente).

**Garduri:** crash-safe loop, lock anti-suprapunere, kill-switch (`internal_scheduler_enabled`), canar pe `/health`, jitter la pornire.

**Pragul abandoned** rămâne 30 min (neatins).

**Dependență:** ADR-5 single-worker — la scalare, scheduler-ul migrează ODATĂ cu SSE/rate-limit.
