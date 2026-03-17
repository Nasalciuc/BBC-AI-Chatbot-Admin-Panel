"""KB Gap Analysis — query pipeline_runs for AI usage, fallbacks, KB coverage.

Usage:
    python scripts/kb_gap_analysis.py

Requires: SUPABASE_URL, SUPABASE_KEY env vars
"""
import asyncio
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ANTHROPIC_API_KEY", "skip")


async def main():
    from app.db.supabase import get_client, _run_sync

    db = get_client()
    print(f"\n## KB Gap Analysis Report — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")

    # 1. Intents hitting AI (should these be templates?)
    print("### AI Usage by Intent\n")
    print("| Intent | AI Calls | Total Cost |")
    print("|--------|----------|-----------|")
    try:
        res = await _run_sync(lambda: db.table("pipeline_runs").select("intent_detected, cost, model_used").in_("model_used", ["haiku", "sonnet"]).execute())
        from collections import Counter, defaultdict
        counts = Counter()
        costs = defaultdict(float)
        for r in (res.data or []):
            intent = r.get("intent_detected", "unknown")
            counts[intent] += 1
            costs[intent] += r.get("cost", 0)
        for intent, count in counts.most_common(10):
            print(f"| {intent} | {count} | ${costs[intent]:.4f} |")
        if not counts:
            print("| (no AI calls recorded yet) | 0 | $0.0000 |")
    except Exception as e:
        print(f"Error querying pipeline_runs: {e}")

    # 2. Fallback messages (chatbot didn't know)
    print("\n### Top Unanswered Questions (had_fallback = true)\n")
    try:
        res = await _run_sync(lambda: db.table("pipeline_runs").select("message_id, intent_detected").eq("had_fallback", True).order("created_at", desc=True).limit(20).execute())
        fallbacks = res.data or []
        if fallbacks:
            for i, fb in enumerate(fallbacks[:10], 1):
                mid = fb.get("message_id")
                intent = fb.get("intent_detected", "?")
                if mid:
                    msg_res = await _run_sync(lambda: db.table("messages").select("content").eq("id", mid).single().execute())
                    content = (msg_res.data or {}).get("content", "?")[:80]
                    print(f"{i}. \"{content}\" — intent: {intent}")
        else:
            print("No fallbacks recorded yet.")
    except Exception as e:
        print(f"Error querying fallbacks: {e}")

    # 3. Current KB coverage
    print("\n### Current KB Coverage\n")
    try:
        res = await _run_sync(lambda: db.table("kb_entries").select("title, tunnel, category_id").eq("is_active", True).execute())
        entries = res.data or []
        print(f"- {len(entries)} active entries")
        sales = [e for e in entries if e.get("tunnel") == "sales"]
        support = [e for e in entries if e.get("tunnel") == "support"]
        print(f"- Sales: {len(sales)} entries")
        print(f"- Support: {len(support)} entries")
        print(f"\nTitles:")
        for e in entries:
            print(f"  - {e.get('title', '?')}")
    except Exception as e:
        print(f"Error querying KB: {e}")

    print("\n### Recommendations\n")
    print("1. Review intents with high AI call count — consider adding templates")
    print("2. Review fallback questions — add KB entries for common unanswered topics")
    print("3. Consider adding: LAX routes, Asian routes, seasonal pricing, first class info")
    print()


if __name__ == "__main__":
    asyncio.run(main())
