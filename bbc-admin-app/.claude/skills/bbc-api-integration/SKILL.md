---
name: bbc-api-integration
description: Connect frontend to FastAPI backend. Use when adding API calls, mock-to-real, or new endpoints. Enforces api.ts gateway and typed responses.
---

# Backend URL: VITE_API_URL → default https://admin-panel-error-production.up.railway.app
# Migration: 1) types.ts 2) api.ts function 3) replace useState+mock with useQuery 4) Skeleton + toast
# Response: { success: boolean, data: T, count: number, error?: string }
# Keys: ['dashboard-stats'] | ['conversations', {page}] | ['leads', {page}] | ['users'] | ['kb-entries', catId]
# NEVER: supabase from frontend | hardcode URLs | useState for server data | skip error/loading
