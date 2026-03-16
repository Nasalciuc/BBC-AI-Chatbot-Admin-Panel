---
name: bbc-api-integration
description: Connect frontend to backend. Use when adding API calls, mock-to-real, new endpoints. Enforces api.ts gateway and typed responses.
---

URL: VITE_API_URL | Response: { success, data, count, error? }
Steps: 1)types.ts 2)api.ts 3)useQuery replaces mock 4)Skeleton+toast
Keys: ['leads',{page}] | ['conversations',{page}] | ['users'] | ['kb-entries',catId]
NEVER: supabase from frontend | hardcode URLs | useState for server | skip loading/error
