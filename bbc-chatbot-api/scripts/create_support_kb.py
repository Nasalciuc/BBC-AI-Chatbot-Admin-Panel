"""Create 15 support KB entries + Travel Information category.

Usage:
    python scripts/create_support_kb.py

Requires: SUPABASE_URL, SUPABASE_KEY env vars
After running: python scripts/embed_kb.py to embed into Qdrant
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ANTHROPIC_API_KEY", "skip")

CHANGES_CAT = "b0000001-0000-0000-0000-000000000004"
BAGGAGE_CAT = "b0000001-0000-0000-0000-000000000005"

ENTRIES = [
    # Changes & Cancellations (2 new, 4 already exist)
    {
        "category_id": CHANGES_CAT,
        "title": "Flight Delay and Cancellation Rights",
        "content": "If your flight is delayed over 3 hours or cancelled, you may be entitled to compensation depending on your route. EU flights are covered by EC 261/2004 (up to \u20ac600). US DOT rules require refund for cancellations. Contact our support team with your booking reference and we'll check your eligibility and handle the claim for you.",
        "tunnel": "support",
    },
    {
        "category_id": CHANGES_CAT,
        "title": "Upgrade Options at Airport",
        "content": "Airport upgrades from economy to business class are sometimes available at check-in or at the gate, typically at 30-50% of the full fare difference. Availability depends on the airline and how full the flight is. Ask at the priority check-in counter or through the airline's app. We can also check upgrade availability for you before your trip.",
        "tunnel": "support",
    },

    # Baggage & Policies (2 new, 2 already exist)
    {
        "category_id": BAGGAGE_CAT,
        "title": "Missing or Delayed Baggage Claim",
        "content": "If your baggage doesn't arrive, file a Property Irregularity Report (PIR) at the airport baggage counter immediately. Most airlines locate and deliver delayed bags within 24-48 hours. Keep receipts for essential purchases \u2014 airlines typically reimburse up to $150-200/day. Contact us with your PIR number for tracking assistance.",
        "tunnel": "support",
    },
    {
        "category_id": BAGGAGE_CAT,
        "title": "WiFi and Entertainment on Board",
        "content": "Most business class long-haul flights include personal entertainment screens (15-18 inches), noise-cancelling headphones, and WiFi. WiFi is complimentary on some airlines (Emirates, JetBlue) or $10-30 per flight on others. Premium streaming content and power outlets are standard in business class on all major carriers.",
        "tunnel": "support",
    },

    # Travel Information (11 new entries -- NEW CATEGORY)
    # category_id will be set after creating the category
    {
        "title": "Seat Selection Options",
        "content": "Business class seats can be selected during booking, at check-in (24h before), or at the airport. Window and aisle are most popular \u2014 business class doesn't have middle seats. Some airlines charge for advance seat selection even in business; others include it. Check your airline's app or contact us with your booking reference.",
        "tunnel": "support",
    },
    {
        "title": "Meal Preferences and Pre-Orders",
        "content": "Business class meals can be pre-ordered 24-72 hours before departure on most airlines. Dietary options typically include vegetarian, vegan, kosher, halal, gluten-free, and low-sodium. Some airlines (like Emirates and Singapore Airlines) offer book-the-cook service with restaurant-quality menus. Check your airline's website or ask us to arrange it.",
        "tunnel": "support",
    },
    {
        "title": "Airport Lounge Access",
        "content": "Your business class ticket includes complimentary access to airline lounges at departure airports. Show your boarding pass at the lounge entrance. Benefits include: complimentary food and drinks, WiFi, showers, quiet areas, and sometimes spa treatments. Some airlines also offer arrival lounges. Priority Pass membership provides access to 1,300+ independent lounges worldwide.",
        "tunnel": "support",
    },
    {
        "title": "Check-in Times and Procedures",
        "content": "Online check-in opens 24 hours before departure for most airlines. At the airport, use the business class priority counter (separate from economy). Recommended arrival: 3 hours before international flights, 2 hours domestic. Business class passengers also get priority security screening and boarding at most airports.",
        "tunnel": "support",
    },
    {
        "title": "Visa and Travel Document Requirements",
        "content": "Visa requirements vary by nationality and destination. Check iatatravelcentre.com or your destination country's embassy website for current requirements. Ensure your passport is valid for at least 6 months beyond your travel date. Transit visas may be required for layovers in certain countries (e.g., USA, Australia, Canada). We recommend checking requirements 4-6 weeks before travel.",
        "tunnel": "support",
    },
    {
        "title": "Travel Insurance Recommendations",
        "content": "We strongly recommend comprehensive travel insurance for international business class trips. Coverage should include: trip cancellation/interruption, medical emergencies, baggage loss, and flight delays. Many premium credit cards include travel insurance when you pay for the ticket with the card. Typical standalone policy costs 4-8% of trip value.",
        "tunnel": "support",
    },
    {
        "title": "Payment Methods and Installments",
        "content": "Buy Business Class accepts all major credit cards (Visa, Mastercard, Amex, Discover), wire transfers, and FlexPay installments (split into 3-6 monthly payments at 0% interest for qualifying bookings). Corporate accounts can arrange monthly invoicing with net-30 terms. Wire transfers receive a 2% discount on bookings over $5,000.",
        "tunnel": "support",
    },
    {
        "title": "How to Request Receipt or Invoice",
        "content": "For receipts or invoices, email support@buybusinessclass.com with your booking reference and preferred email address. Receipts are typically sent within 2 business hours. Corporate invoices include company name, address, and can be formatted for expense reporting. Duplicate receipts for past bookings are available upon request.",
        "tunnel": "support",
    },
    {
        "title": "Special Assistance and Accessibility",
        "content": "Airlines provide special assistance for passengers with reduced mobility, visual or hearing impairments, or medical conditions. Request assistance at least 48 hours before departure. Services include wheelchair assistance, priority boarding, onboard medical equipment, and special meals. Contact us with your booking reference and assistance needs \u2014 we'll coordinate with the airline.",
        "tunnel": "support",
    },
    {
        "title": "Connecting Flights and Layovers",
        "content": "For connecting flights, minimum connection times vary by airport (typically 1.5-3 hours international). Your checked baggage is usually transferred automatically if flights are on the same ticket. During layovers, business class passengers can access airline lounges. If we book your itinerary, we ensure sufficient connection times and handle rebooking if connections are missed.",
        "tunnel": "support",
    },
    {
        "title": "Contact Information and Business Hours",
        "content": "Buy Business Class specialists are available Monday to Friday, 9 AM to 6 PM EST. For urgent booking changes outside business hours, email support@buybusinessclass.com \u2014 we monitor urgent requests 24/7. Sales inquiries: sales@buybusinessclass.com. Corporate accounts: corporate@buybusinessclass.com. Response time: within 2 hours during business hours.",
        "tunnel": "support",
    },
]


async def main():
    from app.db.supabase import get_client, _run_sync

    db = get_client()
    print("Creating support KB entries...\n")

    # Step 1: Create "Travel Information" category
    travel_info_id = "b0000001-0000-0000-0000-000000000006"
    print("Step 1: Creating 'Travel Information' category...")
    try:
        await _run_sync(lambda: db.table("kb_categories").upsert({
            "id": travel_info_id,
            "name": "Travel Information",
            "tunnel": "support",
            "icon": "Info",
            "sort_order": 3,
        }).execute())
        print(f"  OK: Travel Information category (id: {travel_info_id})")
    except Exception as e:
        print(f"  Category may already exist: {e}")

    # Step 2: Insert entries
    print(f"\nStep 2: Inserting {len(ENTRIES)} KB entries...")
    success = 0
    failed = 0
    for entry in ENTRIES:
        if "category_id" not in entry:
            entry["category_id"] = travel_info_id
        try:
            await _run_sync(lambda e=entry: db.table("kb_entries").insert({
                "category_id": e["category_id"],
                "title": e["title"],
                "content": e["content"],
                "tunnel": e["tunnel"],
                "is_active": True,
            }).execute())
            success += 1
            print(f"  OK: {entry['title'][:50]}")
        except Exception as e:
            failed += 1
            print(f"  FAIL: {entry['title'][:50]} -- {e}")

    print(f"\nDone: {success} created, {failed} failed, {len(ENTRIES)} total")
    print("\nNext: run 'python scripts/embed_kb.py' to embed all entries into Qdrant")


if __name__ == "__main__":
    asyncio.run(main())
