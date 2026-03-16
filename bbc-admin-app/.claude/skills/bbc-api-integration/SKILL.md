---
name: bbc-api-integration
description: Patterns for integrating frontend features with the BBC Chatbot API — apiFetch, React Query, type safety
applyTo: "src/lib/**,src/features/**"
---

# BBC API Integration Skill

## Purpose

Ensure consistent, type-safe API integration between the BBC Admin Panel and the BBC Chatbot API backend.

## API Client (`src/lib/api.ts`)

All HTTP calls go through `apiFetch<T>()`:

```typescript
import { apiFetch } from '@/lib/api'
import type { Conversation } from '@/lib/types'

const data = await apiFetch<Conversation[]>('/api/admin/conversations?page=1&limit=20')
```

### How apiFetch works

1. Prepends `VITE_API_URL` to the path.
2. Adds HTTP Basic Auth header from `VITE_API_USER` / `VITE_API_PASS`.
3. Sets 8-second timeout via `AbortController`.
4. Returns typed `T` from the JSON response.

## Response Envelope

All backend endpoints return:

```json
{
  "success": true,
  "data": [],
  "count": 0,
  "error": null
}
```

Always destructure:

```typescript
const response = await apiFetch<{ success: boolean; data: T[]; count: number; error: string | null }>(url)
if (!response.success) throw new Error(response.error ?? 'Unknown error')
return response.data
```

## React Query Patterns

### Query hook

```typescript
// src/features/leads/queries.ts
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'

export function useLeads(params: LeadQueryParams) {
  return useQuery({
    queryKey: ['leads', params],
    queryFn: () => api.leads.getAll(params),
    staleTime: 30_000,
  })
}
```

### Mutation hook

```typescript
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { toast } from 'sonner'

export function useUpdateLead() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: UpdateLeadInput) => api.leads.update(data.id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['leads'] })
      toast.success('Lead updated')
    },
    onError: (error) => {
      toast.error(error.message)
    },
  })
}
```

### Query key conventions

| Domain | Key pattern |
|--------|------------|
| Dashboard | `['dashboard', 'stats']` |
| Conversations | `['conversations', { page, status, tunnel }]` |
| Leads | `['leads', { page, tier, status }]` |
| Knowledge Base | `['kb', 'categories']` or `['kb', 'entries', categoryId]` |
| Users | `['users']` |

## Type Safety

Types in `src/lib/types.ts` MUST mirror the backend Pydantic models in `bbc-chatbot-api/app/models/`.

When adding a new endpoint:

1. Check `specs/api-contract.md` for the response shape.
2. Add/update TypeScript types in `src/lib/types.ts`.
3. Add the fetch function in `src/lib/api.ts`.
4. Create query/mutation hooks in `src/features/<domain>/queries.ts`.

## Error Handling

```typescript
import { handleServerError } from '@/lib/handle-server-error'

// In components
try {
  await mutateAsync(data)
} catch (error) {
  handleServerError(error)
}
```

- Network errors → toast with retry suggestion
- 401/403 → redirect to login
- 422 → show field-level validation errors
- 500 → generic error toast

## Adding a New Endpoint

Checklist:

1. [ ] Endpoint documented in `specs/api-contract.md`
2. [ ] TypeScript types added to `src/lib/types.ts`
3. [ ] Fetch function added to `src/lib/api.ts`
4. [ ] React Query hook in `src/features/<domain>/queries.ts`
5. [ ] Error handling via `handleServerError`
6. [ ] Loading state handled (skeleton or spinner)

## Do NOT

- Call `fetch()` directly — always use `apiFetch`.
- Put API URLs in component files — centralise in `api.ts`.
- Cache server data in Zustand — use React Query cache.
- Skip error handling — every query needs loading + error states.
