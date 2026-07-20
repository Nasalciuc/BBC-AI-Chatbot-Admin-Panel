# Teams — Raport total (Faza 1 · 1.5 · 2 · 3)

**Date:** 2026-07-20  
**Branch:** `feat/teams-phase-1` (working tree; **necommitat**)  
**Prod Supabase:** `bbc-chatbot` (`exwxdjfeoekfixnjsreq`)

---

## Verdict pe o linie

| Fază | Ce este | Status |
|------|---------|--------|
| **1** — model + rol + CRUD API + FE wiring | Cod + migrare | **DONE** (cod necommitat; migrare **aplicată** în prod) |
| **3** — UI management `/teams` | Frontend | **DONE** (cod necommitat; build PASS) |
| **1.5** — seed real: echipe + asignări | Date operaționale | **NOT STARTED** (0 echipe, 0 useri cu `team_id`) |
| **2** — filtrare pe echipă (conversations/leads/lists) | Backend scoping | **NOT STARTED** (blocat până după 1.5) |

**Ordinea corectă de ship:** Phase 1 merge+deploy → Phase 3 UI live → **Phase 1.5 seed** → **apoi** Phase 2 filtering.

---

## Snapshot prod (live, 2026-07-20)

| Metric | Valoare |
|--------|---------|
| Tabel `public.teams` există | **DA** |
| Echipe totale / active | **0 / 0** |
| Useri cu `team_id` setat | **0** |
| Useri `project_manager` | **0** |
| Roluri active acum | owner 2 · admin 3 · qa 1 · sales 45 · supervisor 6 |

Concluzie: **schema e gata; datele de business nu.** Fără Phase 1.5, Phase 2 ar produce ecrane goale pentru supervisor/PM.

---

## Faza 1 — DONE (implementare + validare live)

### Ce include
- Migrare `021_teams.sql`: rol `project_manager`, tabel `teams`, `team_id` pe `users` / `conversations` / `leads`, indexes, RLS pe `teams`
- Backend CRUD: `GET/POST/PATCH/DELETE /api/admin/teams`
- Assign: `PATCH /api/admin/users/{id}` cu `team_id` (inclusiv `null`)
- Role wiring: PM în management/list; **PM NU** în `leads.py` (trap 403); PM ∉ PRIVILEGED
- FE: tipuri, permissions (`canViewTeams` / `canManageTeams`), rol selectabil, supervisor `canReadMessages=true` (interim tunnel-wide)

### Validare
| Test | Rezultat |
|------|----------|
| `tests/test_teams.py` | **16/16 PASS** |
| Live read-only pe prod DB (GET teams / leads trap) | **PASS** |
| Live write E2E + full cleanup (create/assign/409 guards/delete) | **12/12 PASS**, 0 urme în prod |
| Frontend build (împreună cu Phase 3) | **PASS** |

### Caveat deschis (decizie owner)
Până la Phase 2, supervisor cu `canReadMessages=true` citește conversații pe **tot tunnel-ul**, nu doar pe echipa lui. Acceptat temporar sau defer FE-3.2.2 până la Phase 2?

### Deploy gap
- Migrarea **e în prod**
- Codul API Teams **nu e pe Railway** (doar working tree local)
- Codul FE Teams **nu e pe Vercel** (doar working tree)

---

## Faza 3 UI — DONE (frontend management)

### Ce include
- Ruta `/teams`, sidebar gated pe `canManageTeams` (owner/admin/dev)
- Create / edit team (name, shift, supervisor, PM)
- Manage members (assign/unassign operators)
- Deactivate (soft delete) cu guard 409 dacă are membri
- Client API în `src/lib/api.ts` (`getTeams`, `createTeam`, `updateTeam`, `deleteTeam`, `assignUserToTeam`)

### Validare
- Build TS: **PASS**
- Live click-through UI vs API: **nerealizat încă** (API Teams nu e deployat; 0 echipe reale)

### Dependențe
Consumă Phase 1 endpoints. Fără merge+deploy Phase 1, UI-ul e mort în prod.

---

## Faza 1.5 — NOT STARTED (seed operațional)

### Scop
Creează date reale **înainte** de filtrare:
1. Creează echipe (nume + shift + supervisor ± PM)
2. Asignează operatori (`sales`/`support`) pe `team_id`
3. (opțional) promovează ≥1 user la `project_manager` dacă vrei rolul PM live
4. Verifică că fiecare supervisor activ are exact o echipă; operatorii relevanți nu mai sunt `team_id=NULL`

### De ce e obligatorie înainte de Phase 2
Phase 2 filtrează listele pe `team_id` / membership. Dacă filtrezi cu 0 echipe și 0 asignări → supervisor/PM văd **liste goale** (false “nimic de lucru”).

### Stare curentă
| Item | Status |
|------|--------|
| Echipe reale | **0** |
| Asignări `users.team_id` | **0** |
| User `project_manager` | **0** |
| Script/UI seed rulat | **NU** |

### Cum se face (când ești gata)
- Din UI `/teams` (după deploy Phase 1+3), **sau**
- Prin API owner (`POST /admin/teams` + `PATCH /admin/users/{id}`), **sau**
- Seed SQL/script controlat (cu listă de echipe + UUID-uri confirmate de owner)

### Definition of Done (1.5)
- [ ] ≥1 echipă activă per shift/grup planificat
- [ ] Fiecare supervisor relevant are exact 1 echipă activă
- [ ] Operatorii relevanți au `team_id` setat
- [ ] (opțional) ≥1 PM asignat pe echipe
- [ ] Smoke: owner listează echipe; supervisor vede doar a lui; delete cu membri → 409

---

## Faza 2 — NOT STARTED (filtrare pe echipă)

### Scop
Backend (și FE dacă e nevoie) **îngustează** vizibilitatea:
- **Supervisor** → conversații / operatori / (eventual leads) doar din echipa lui
- **Project Manager** → echipele pe care le are ca `pm_id` (fără leads — trap rămâne)
- **Owner/admin/dev** → neschimbat (văd tot)
- **Sales/support** → neschimbat pe tunnel; opțional filtrare “my team mates” ulterior

### Unde e astăzi (explicit “Phase 2 later”)
În `conversations.py`:
```text
privileged users can filter freely (PM: no team scoping yet — Phase 2)
```
Nu există încă filtre pe `team_id` în listări de conversații/leads/dashboard.

### Ce trebuie construit (estimat)
1. Helper comun: `resolve_team_scope(user) → {mode: all|team_ids|none, team_ids: [...]}`
2. Aplicare pe:
   - `GET /api/conversations` (+ detail/read messages)
   - (decizie) `GET /api/leads` pentru supervisor — da/nu pe team
   - listări users pentru PM/supervisor (dacă trebuie “doar oamenii mei”)
   - dashboard metrics (dacă sunt globale acum și trebuie scoped)
3. Reguli edge:
   - user fără `team_id` / supervisor fără echipă → listă goală sau fallback documentat
   - conversații cu `team_id` NULL (istoric) → politică: invizibile pt team-scoped / vizibile doar privileged
   - reassign / engage: păstrează sau setează `team_id` pe conversation?
4. Teste: unit + live smoke pe roluri reale după 1.5
5. FE: eventual badge “Team X”, filtre UI; gating-ul existent rămâne

### Precondiții (gate)
- [ ] Phase 1 **merged + deployed** pe Railway
- [ ] Phase 3 UI **merged + deployed** pe Vercel (opțional pt filtrare, util pt seed)
- [ ] **Phase 1.5 DONE** (echipe + asignări reale)
- [ ] Decizie pe interim supervisor read (accept / revert)
- [ ] Decizie: leads scoped pe team pentru supervisor? (PM rămâne 403)

### Definition of Done (2)
- [ ] Supervisor vede doar conversațiile echipei (nu tot tunnel-ul)
- [ ] PM vede doar echipele lui; leads tot 403
- [ ] Owner/admin neschimbați
- [ ] Suite teste Phase 2 + smoke live pe useri reali
- [ ] Documentat comportament pentru `team_id` NULL pe conversații vechi

---

## Matrice “ce e live vs local”

| Componentă | Local (working tree) | Prod DB | Railway API | Vercel FE |
|------------|----------------------|---------|-------------|-----------|
| Migrare 021 | fișier prezent | **aplicată** | n/a | n/a |
| Teams CRUD API | **da** | n/a | **nu** | n/a |
| PM role + permissions FE | **da** | rol posibil, 0 useri | **nu** | **nu** |
| UI `/teams` | **da** | n/a | n/a | **nu** |
| Echipe/asignări | n/a | **0** | n/a | n/a |
| Team filtering (Phase 2) | **nu** | **nu** | **nu** | **nu** |

---

## Riscuri / decizii deschise

1. **Commit/PR:** Phase 1 + Phase 3 sunt în același working tree pe `feat/teams-phase-1`, necommitate. Recomandat: commit/PR separat sau un PR “Teams Phase 1+3” clar.
2. **Deploy gap:** DB e înaintea codului — inofensiv (coloane NULL, tabel gol), dar UI/API Teams nu funcționează în prod până la deploy.
3. **Supervisor interim widening:** tunnel-wide message read până la Phase 2.
4. **Fără PM în prod:** Phase 2 PM-scoping nu poate fi validat E2E până promovezi pe cineva.
5. **`gh` auth:** PR-urile automate au fost blocate istoric; nevoie `gh auth login` sau PR manual.
6. **Nu ship Phase 2 înainte de 1.5** — e cel mai important gate operațional.

---

## Next actions recomandate (ordine)

1. **Commit + PR** Phase 1 (+ optional Phase 3 în același PR sau separat)
2. **Deploy** API (Railway) + FE (Vercel)
3. **Phase 1.5:** creează echipe reale + asignează supervisori/operatori (UI `/teams`)
4. (opțional) promovează 1× `project_manager`
5. **Phase 2:** implementare filtrare + teste pe date reale
6. Decide interim supervisor `canReadMessages`

---

## Referințe

- `docs/reports/TEAMS_PHASE1_REPORT.md`
- `docs/reports/TEAMS_PHASE3_UI_REPORT.md`
- Live E2E 2026-07-20: read-only PASS + write-path **12/12** cu cleanup (0 urme)
- Migrare: `bbc-chatbot-api/migrations/021_teams.sql`
- API: `bbc-chatbot-api/app/api/teams.py`
- UI: `bbc-admin-app/src/features/teams/`
