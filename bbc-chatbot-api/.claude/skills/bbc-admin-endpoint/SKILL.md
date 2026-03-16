---
name: bbc-admin-endpoint
description: Create admin CRUD endpoints. Use when adding routes or db queries. Enforces async wrapping, response shape, auth.
---

All endpoints: asyncio.to_thread() for supabase, response { success, data, count, error }, Depends(get_current_user).
See bbc-backend-architect agent for List/Detail/Update patterns.
