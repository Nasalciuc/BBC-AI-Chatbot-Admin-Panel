---
name: bbc-copilot-prompt
description: Prompt engineering patterns for GitHub Copilot and Claude Code when working on the BBC Admin Panel
applyTo: "**"
---

# BBC Copilot Prompt Skill

## Purpose

Maximise Claude Code / GitHub Copilot output quality when generating code for this project by applying project-specific prompt patterns.

## Context Injection

When starting a new task, always load these files first:

1. `CLAUDE.md` — project rules and stack overview
2. `src/lib/types.ts` — canonical data shapes
3. `src/lib/api.ts` — API client patterns
4. The relevant `src/features/<domain>/` folder listing

## Prompt Templates

### New Feature Page

```
Create a new feature page for <DOMAIN> at src/features/<domain>/index.tsx.

Requirements:
- Use TanStack React Query to fetch data from api.<domain>.<method>()
- Use DataTable with columns defined in src/features/<domain>/columns.tsx
- Follow the page layout pattern from bbc-frontend-design skill
- Add toolbar with search and filters
- Include loading skeleton and error state
- Types are already in src/lib/types.ts as <TypeName>
```

### New API Endpoint Hook

```
Create a React Query hook in src/features/<domain>/queries.ts:

- GET endpoint: api.<domain>.getAll(params)
- Query key: ['<domain>', params]
- Stale time: 30 seconds
- Return { data, isLoading, error }
- Type the response as ApiResponse<TypeName[]>
```

### New Dialog / Form

```
Create a <Action><Entity>Dialog component:

- Use Dialog from src/components/ui/dialog
- Form via React Hook Form with Zod schema
- Fields: <list fields and types>
- On submit: call api.<domain>.<method>() and invalidate query cache
- Show toast on success/error via sonner
- Use useDialogState hook pattern
```

## Code Review Checklist Prompt

When reviewing generated code, verify:

```
Review this code against BBC Admin Panel conventions:
1. Uses @/* path alias (not relative ../../)?
2. Types match src/lib/types.ts?
3. API calls go through src/lib/api.ts?
4. UI uses Radix components from src/components/ui/?
5. Forms use Zod + React Hook Form?
6. No 'any' types?
7. Named exports (not default)?
8. Feature code in src/features/<domain>/?
```

## Anti-Patterns to Avoid

| Bad | Good |
|-----|------|
| `fetch()` directly | `apiFetch<T>()` from `@/lib/api` |
| `useState` for server data | `useQuery` from React Query |
| Raw Radix imports | Wrapped components from `@/components/ui/` |
| `export default` | `export function ComponentName` |
| Console.log for errors | Toast via `sonner` + `handleServerError` |
| Manual form validation | Zod schema + `useForm` resolver |

## Iterative Refinement

When Claude produces suboptimal code:

1. Point at the specific convention violated (reference CLAUDE.md section).
2. Show the correct pattern (reference the relevant skill).
3. Ask for regeneration of only the violating section.
