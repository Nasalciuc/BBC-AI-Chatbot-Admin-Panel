"""One-time script: populate Qdrant from Supabase KB entries.

Usage:
    python scripts/embed_kb.py

Requires env vars: SUPABASE_URL, SUPABASE_KEY, QDRANT_URL, QDRANT_API_KEY
"""
import sys
import os
import asyncio

# Allow imports from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import settings
from app.db.qdrant import ensure_collection, upsert_kb_entry, count_points
from app.db.supabase import get_kb_entries


async def main():
    # ── Verify config ──
    missing = []
    if not settings.supabase_url: missing.append("SUPABASE_URL")
    if not settings.supabase_key: missing.append("SUPABASE_KEY")
    if not settings.qdrant_url:   missing.append("QDRANT_URL")
    if not settings.qdrant_api_key: missing.append("QDRANT_API_KEY")
    if missing:
        print(f"ERROR: Missing env vars: {', '.join(missing)}")
        sys.exit(1)

    print(f"Qdrant URL:    {settings.qdrant_url}")
    print(f"Collection:    {settings.qdrant_collection}")
    print()

    # ── Step 1: Recreate collection at 384d ──
    print("Step 1: Recreating collection (384d, Cosine)...")
    ok = await ensure_collection()
    if not ok:
        print("ERROR: Failed to create Qdrant collection")
        sys.exit(1)
    print("  ✅ Collection ready")
    print()

    # ── Step 2: Fetch KB entries from Supabase ──
    print("Step 2: Fetching KB entries from Supabase...")
    entries = await get_kb_entries(is_active=True, limit=500)
    if not entries:
        print("  ⚠️  No KB entries found in Supabase")
        sys.exit(0)
    print(f"  Found {len(entries)} entries")
    print()

    # ── Step 3: Upsert each entry into Qdrant ──
    print("Step 3: Upserting entries into Qdrant...")
    succeeded = 0
    failed = 0
    for i, entry in enumerate(entries, 1):
        entry_id = entry.get("id", "")
        title = entry.get("title", "")
        content = entry.get("content", "")
        tunnel = entry.get("tunnel", "sales")
        category_id = entry.get("category_id")

        ok = await upsert_kb_entry(entry_id, title, content, tunnel, category_id)
        if ok:
            succeeded += 1
        else:
            failed += 1
            print(f"  ❌ Failed: {entry_id} — {title[:50]}")

        if i % 10 == 0:
            print(f"  Progress: {i}/{len(entries)}")

    print()

    # ── Step 4: Verify count ──
    print("Step 4: Verifying...")
    point_count = await count_points()
    print(f"  Points in collection: {point_count}")
    print()

    # ── Report ──
    print("=" * 50)
    print(f"  Embedded {len(entries)} entries")
    print(f"  ✅ Succeeded: {succeeded}")
    if failed:
        print(f"  ❌ Failed:    {failed}")
    else:
        print(f"  ❌ Failed:    0")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
