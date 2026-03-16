# API Contract — Admin Endpoints

> Source of truth for frontend-backend communication.
> Frontend (`bbc-admin-app/src/lib/types.ts`) must mirror these shapes.

## Response Envelope

ALL admin endpoints return:

```json
{
  "success": boolean,
  "data": T | T[],
  "count": number,
  "error": string | null
}
```

## Endpoints

### GET /api/admin/stats
Dashboard KPIs. Already implemented. See `app/routes/admin.py`.

### GET /api/admin/conversations
- Query: `?page=1&limit=20&status=active|pending|closed&tunnel=sales|support`
- Response data: `Conversation[]` with last message preview
- **Status:** NOT YET IMPLEMENTED

### GET /api/admin/conversations/{id}
- Response data: `Conversation` with full `messages[]`
- **Status:** NOT YET IMPLEMENTED

### GET /api/admin/leads
- Query: `?page=1&limit=20&tier=gold|silver|bronze&status=new|contacted|qualified|converted|lost`
- Response data: `Lead[]`
- **Status:** NOT YET IMPLEMENTED

### GET /api/admin/users
- Auth: requires `owner` or `admin` role
- Response data: `User[]`
- **Status:** NOT YET IMPLEMENTED

### GET /api/admin/kb/categories
- Response data: `KBCategory[]`
- **Status:** NOT YET IMPLEMENTED

### GET /api/admin/kb/entries
- Query: `?category_id=uuid`
- Response data: `KBEntry[]`
- **Status:** NOT YET IMPLEMENTED
