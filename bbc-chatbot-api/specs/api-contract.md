# API Contract — ALL return { success, data, count, error? }

| Method | Endpoint | Status |
|--------|----------|--------|
| GET | /api/admin/stats | ✅ DONE |
| GET | /api/admin/conversations | ✅ DONE |
| GET | /api/admin/conversations/{id} | ✅ DONE |
| PATCH | /api/admin/conversations/{id} | ✅ DONE |
| GET | /api/admin/leads | ✅ DONE |
| GET | /api/admin/leads/{id} | ❌ TODO |
| PATCH | /api/admin/leads/{id} | ❌ TODO |
| GET | /api/admin/users | ✅ DONE |
| PATCH | /api/admin/users/{id} | ✅ DONE |
| GET | /api/admin/kb/categories | ✅ DONE |
| GET | /api/admin/kb/entries | ✅ DONE |
| POST | /api/admin/kb/entries | ✅ DONE |
| PUT | /api/admin/kb/entries/{id} | ✅ DONE |

---

## GET /api/admin/leads

List leads with pagination and filters.

**Query Parameters:**
| Param | Type | Default | Validation |
|-------|------|---------|------------|
| status | string? | null | `new\|contacted\|qualified\|converted\|lost` |
| tier | string? | null | `gold\|silver\|bronze` |
| tunnel | string? | null | `sales\|support` |
| search | string? | null | max 100 chars, searches name/email/phone/route |
| limit | int | 50 | 1–200 |
| offset | int | 0 | ≥ 0 |

**Response 200:**
```json
{
  "success": true,
  "data": [LeadListItem],
  "count": 42,
  "limit": 50,
  "offset": 0
}
```

**LeadListItem fields:** id, conversation_id, trip_type, cabin_class, passengers, flexible_dates, origin_code, destination_code, departure_date, return_date, route_display, score, tier, status, intent_signals, notes, created_at, updated_at, contacted_at, converted_at, visitor_name, visitor_email, visitor_phone

**Error responses:** 401 (no auth), 422 (invalid params)

---

## GET /api/admin/conversations

List conversations with pagination and filters.

**Query Parameters:**
| Param | Type | Default | Validation |
|-------|------|---------|------------|
| tunnel | string? | null | `sales\|support` |
| status | string? | null | `active\|pending\|closed` |
| search | string? | null | max 100 chars |
| limit | int | 50 | 1–200 |
| offset | int | 0 | ≥ 0 |

**Response 200:**
```json
{
  "success": true,
  "data": [ConversationListItem],
  "count": 42
}
```

**ConversationListItem fields:** id, tunnel, mode, status, visitor_name, visitor_email, visitor_phone, assigned_agent_id, message_count, ai_cost_total, created_at, updated_at, closed_at

**Error responses:** 401 (no auth), 422 (invalid params)

---

## GET /api/admin/conversations/{id}

Get single conversation with messages.

**Response 200:**
```json
{
  "success": true,
  "data": ConversationDetail,
  "count": 1
}
```

**Response 404:**
```json
{
  "success": false,
  "data": null,
  "count": 0,
  "error": "Not found"
}
```

**ConversationDetail fields:** all ConversationListItem fields + messages (array of MessageItem), metadata

**Error responses:** 401 (no auth)

---

## GET /api/admin/kb/categories

List KB categories with entry counts.

**Query Parameters:**
| Param | Type | Default | Validation |
|-------|------|---------|------------|
| tunnel | string? | null | `sales\|support` |

**Response 200:**
```json
{
  "success": true,
  "data": [KBCategoryItem],
  "count": 5
}
```

**KBCategoryItem fields:** id, name, tunnel, icon, sort_order, entry_count

**Error responses:** 401 (no auth), 422 (invalid params)

---

## GET /api/admin/kb/entries

List KB entries with filters.

**Query Parameters:**
| Param | Type | Default | Validation |
|-------|------|---------|------------|
| tunnel | string? | null | |
| category_id | string? | null | |
| is_active | bool? | null | |
| limit | int | 100 | 1–500 |

**Response 200:**
```json
{
  "success": true,
  "data": [KBEntryItem],
  "count": 12
}
```

**KBEntryItem fields:** id, category_id, title, content, tunnel, is_active, view_count, created_at, updated_at

**Error responses:** 401 (no auth)

---

## POST /api/admin/kb/entries

Create a new KB entry.

**Request body:** `{ category_id, title, content, tunnel, is_active? }`

**Response 201:**
```json
{
  "success": true,
  "data": KBEntryItem,
  "count": 1
}
```

**Error responses:** 401 (no auth), 422 (invalid body)

---

## PUT /api/admin/kb/entries/{id}

Update an existing KB entry.

**Request body:** `{ title?, content?, is_active?, category_id? }` (partial)

**Response 200:**
```json
{
  "success": true,
  "data": KBEntryItem,
  "count": 1
}
```

**Not found:**
```json
{
  "success": false,
  "data": null,
  "count": 0,
  "error": "KB entry not found"
}
```

**Error responses:** 401 (no auth), 400 (no fields), 422 (invalid body)
