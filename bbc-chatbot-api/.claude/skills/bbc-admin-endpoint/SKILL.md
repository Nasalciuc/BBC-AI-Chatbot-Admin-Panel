---
name: bbc-admin-endpoint
description: Create admin CRUD endpoints. Use when adding routes or db queries. Enforces async, shape, auth.
---

All: asyncio.to_thread() | { success, data, count, error } | Depends(get_current_user)
See bbc-backend-architect for List/Detail/Update patterns.
