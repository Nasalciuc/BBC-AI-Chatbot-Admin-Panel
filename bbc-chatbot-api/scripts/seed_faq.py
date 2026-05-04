"""
Seed JIVO FAQ entries into Qdrant kb_entries collection.
Uses the same upsert pattern as app/db/qdrant.py.

Run: python -m scripts.seed_faq
Or:  cd bbc-chatbot-api && python scripts/seed_faq.py
"""

import uuid
import httpx
import os
from dotenv import load_dotenv

load_dotenv()

QDRANT_URL = os.getenv("QDRANT_URL", "").rstrip("/")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "")
COLLECTION = os.getenv("QDRANT_COLLECTION", "kb_entries")
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

FAQ_ENTRIES = [
    # === TRUST / LEGITIMACY ===
    {
        "title": "FAQ: Who are you working with?",
        "content": "Q: Who are you working with?\nA: We work with the biggest consolidators worldwide and directly with the airlines to obtain special private discounted fares.",
        "tunnel": "sales",
        "category_id": "faq",
    },
    {
        "title": "FAQ: Why are your tickets cheaper?",
        "content": "Q: Why are your tickets cheaper than on other websites?\nA: The benefit of using our services is the ability to purchase unpublished and private fares that other online travel websites are not allowed to sell.",
        "tunnel": "sales",
        "category_id": "faq",
    },
    {
        "title": "FAQ: Are tickets frequent flyer miles?",
        "content": "Q: Are your tickets actually frequent flyer miles purchased from another party?\nA: No, our tickets are revenue tickets sold directly from the Global Distribution System (GDS) that gives you the ability to accumulate miles on partner and affiliate airlines.",
        "tunnel": "sales",
        "category_id": "faq",
    },
    {
        "title": "FAQ: Seems like a scam",
        "content": "Q: How you sell your tickets seems like a scam.\nA: We are an accredited and genuine travel consultancy. We are IATA accredited (#14531683), BBB accredited, and rated Excellent on Trustpilot. We sell unpublished and private fares that other online travel websites are not allowed to sell.",
        "tunnel": "sales",
        "category_id": "faq",
    },
    {
        "title": "FAQ: Never heard about your company",
        "content": "Q: Never heard about your company, tell me more.\nA: We are an accredited travel consultancy in partnerships with the major air consolidators and airlines. We are IATA accredited, BBB rated, and hold an Excellent rating on Trustpilot from real customers. Our US headquarters is in Chicago, IL.",
        "tunnel": "sales",
        "category_id": "faq",
    },
    {
        "title": "FAQ: Bad reviews on Trustpilot",
        "content": "Q: Your reviews are pretty bad, how can I trust you?\nA: Those experiences are from a very small number out of thousands of customers, mostly during challenging times when airline companies were constantly changing refund rules. We have not let down any customer — once airlines processed refunds, they immediately reached customer accounts. We are rated Excellent on Trustpilot overall. Give us a chance — no commitment required.",
        "tunnel": "sales",
        "category_id": "faq",
    },
    {
        "title": "FAQ: Not refunding cancelled flights",
        "content": "Q: I see on Trustpilot that you have not been refunding people for cancelled flights.\nA: We strictly adhere to policies and rules from airline companies. During past challenging periods, airlines constantly changed refund rules. We have not let down any customer — once airlines refunded the money, it reached our customers' accounts. We have shown no intention of keeping anyone's money.",
        "tunnel": "sales",
        "category_id": "faq",
    },
    {
        "title": "FAQ: Is your company a third party consolidator?",
        "content": "Q: Is your company a third party ticket consolidator?\nA: We are a wholesaler who works directly with consolidators and airlines to find the best fares for our customers.",
        "tunnel": "sales",
        "category_id": "faq",
    },
    # === HOW IT WORKS ===
    {
        "title": "FAQ: How does it work?",
        "content": "Q: How does it work? Can you explain?\nA: Our business model requires you to complete a form with your travel details so our consultants can prepare customized deals for your request. It is an efficient and time-saving way to find the best private fares. A consultant will reach out within 30 minutes with personalized options.",
        "tunnel": "sales",
        "category_id": "faq",
    },
    {
        "title": "FAQ: Why can't I wait in chat for quotes?",
        "content": "Q: I like the chat option, why can't I wait here to get the quotes?\nA: Our consultants build flights manually from multiple content sources to guarantee you the best unpublished deal. This process takes some time but ensures you get a competitive fare you would not find on public booking sites.",
        "tunnel": "sales",
        "category_id": "faq",
    },
    {
        "title": "FAQ: Can I see itinerary without entering details?",
        "content": "Q: Can I see the itinerary without entering my details?\nA: Since we have a wide range of special deals and the market is always dynamic, we do not know which airline will offer the best price at any specific moment. Sharing your details allows our consultants to prepare personalized options tailored to your preferences.",
        "tunnel": "sales",
        "category_id": "faq",
    },
    {
        "title": "FAQ: Why different than Skyscanner?",
        "content": "Q: Why are you different than Skyscanner or other online travel engines?\nA: Unlike Skyscanner or other online engines, we build flights manually from multiple content sources which guarantees you an unpublished competitive deal that is not available on public search engines.",
        "tunnel": "sales",
        "category_id": "faq",
    },
    {
        "title": "FAQ: Came through Kayak, are these charter flights?",
        "content": "Q: I got to this page through Kayak, are these charter flights?\nA: We have an advertising partnership with Kayak. These are regular commercial flights booked from the Global Distribution System — not charters.",
        "tunnel": "sales",
        "category_id": "faq",
    },
    # === PRICING ===
    {
        "title": "FAQ: Is the price guaranteed?",
        "content": "Q: The price I see is guaranteed?\nA: Yes, the price is guaranteed and includes all taxes and fees. However, due to high demand, it may not be available on all dates. A consultant can confirm availability for your specific travel dates.",
        "tunnel": "sales",
        "category_id": "faq",
    },
    {
        "title": "FAQ: Agent price is more expensive than website",
        "content": "Q: The price online is fine but your agent sent a more expensive option.\nA: Your agent most likely offered flights based on your specific preferences and schedule. If you are interested in the price shown on our website, we would be glad to have your agent offer you those fares as well.",
        "tunnel": "sales",
        "category_id": "faq",
    },
    {
        "title": "FAQ: Why no airline or stop details listed?",
        "content": "Q: Why don't you list details about the flights — airlines, number of stops?\nA: Since we have a wide range of special deals and the market is always dynamic, we do not know which airline will offer the best price at any given moment. Tell us your airline preferences and we will narrow down exclusive deals for you.",
        "tunnel": "sales",
        "category_id": "faq",
    },
    {
        "title": "FAQ: How can I tell which airline?",
        "content": "Q: How can I tell which airline you are quoting?\nA: We work with all major airlines and select the best option based on your preferences. Tell us more about your airline preferences to narrow down some exclusive deals for you.",
        "tunnel": "sales",
        "category_id": "faq",
    },
    {
        "title": "FAQ: Service fee?",
        "content": "Q: Do you have a service fee?\nA: No, our service is completely free of charge. You only pay for the flight ticket itself.",
        "tunnel": "sales",
        "category_id": "faq",
    },
    # === PRODUCT KNOWLEDGE ===
    {
        "title": "FAQ: What is business class?",
        "content": "Q: What is business class exactly?\nA: Business class is the premium product for air travel offering the best balance of cost, comfort, and value. It includes Business Class Lounge Access, Priority Boarding, Flat Bed Seat, High-end Menu On-board, and many other benefits.",
        "tunnel": "sales",
        "category_id": "faq",
    },
    {
        "title": "FAQ: Business class vs first class",
        "content": "Q: What is the difference between business class and first class?\nA: Business Class offers a premium travel experience with lounge access, flat bed seats, and high-end dining. First Class takes it further with an extremely luxurious and private experience — even more comfort, space, and personal dedication.",
        "tunnel": "sales",
        "category_id": "faq",
    },
    {
        "title": "FAQ: What is a non-stop flight?",
        "content": "Q: What is a non-stop flight?\nA: A non-stop flight goes directly from your origin to your destination without stopping in another city. It is the fastest and most convenient way to travel.",
        "tunnel": "sales",
        "category_id": "faq",
    },
    {
        "title": "FAQ: Travel insurance and Ticket Protection",
        "content": "Q: Do you offer travel insurance?\nA: We have a product called Ticket Protection, which provides a less restrictive fare allowing refunds and exchanges due to medical reasons. Standard travel insurance is also available upon request. Our travel experts can explain all options.",
        "tunnel": "sales",
        "category_id": "faq",
    },
    # === WHY SHARE CONTACT ===
    {
        "title": "FAQ: Why should I give my phone number?",
        "content": "Q: Why should I provide my personal phone number?\nA: Our agents will not bother you with phone calls — they may call just to confirm details and discuss flight options for a personalized service. You can also communicate via text or SMS. Sometimes emails go to spam folders, so a brief call ensures you do not miss a great flight option.",
        "tunnel": "sales",
        "category_id": "faq",
    },
    # === REFUNDS / POLICY ===
    {
        "title": "FAQ: Are tickets refundable?",
        "content": "Q: Will the ticket be refundable?\nA: Refundable fares are available upon request. By default, the most competitive offers are non-refundable. However, our Ticket Protection product makes the fare less restrictive, allowing refunds and exchanges. A travel expert can explain all your options.",
        "tunnel": "sales",
        "category_id": "faq",
    },
    # === COMPANY INFO ===
    {
        "title": "FAQ: How long in industry?",
        "content": "Q: How long have you been in the industry?\nA: We have been in the industry for over 5 years, and our travel consultants have extensive experience and travel backgrounds. Our goal is customer satisfaction at every level of interaction.",
        "tunnel": "sales",
        "category_id": "faq",
    },
    {
        "title": "FAQ: What booking systems do you use?",
        "content": "Q: What systems do you use to sell tickets?\nA: We use major distribution systems including Sabre where we have exclusive content uploaded directly from our consolidators and airline partners.",
        "tunnel": "sales",
        "category_id": "faq",
    },
    {
        "title": "FAQ: IATA accreditation code",
        "content": "Q: What is your IATA code? Are you IATA accredited?\nA: Yes, we are fully IATA accredited. Our IATA code is 14531683. You can verify this at the IATA verification portal.",
        "tunnel": "sales",
        "category_id": "faq",
    },
    # === MASON COMPANY OVERVIEW ===
    {
        "title": "Company: BBC Credentials and Trust",
        "content": "BuyBusinessClass.com is a premium travel agency specializing in discounted business and first class flights. We are IATA accredited (#14531683), TRUE accredited (#99910753), BBB accredited, and rated Excellent on Trustpilot. Our US headquarters is at 180 North Stetson Avenue, Chicago, IL 60601. We are available 24/7.",
        "tunnel": "sales",
        "category_id": "company",
    },
    {
        "title": "Company: How BBC Works — 6-Stage Process",
        "content": "BuyBusinessClass operates a 6-stage process: 1) Fast Response within 30 minutes, 2) Smart Discovery to learn travel style and budget, 3) Expert Sourcing using Sabre to find private rates, 4) Phone Presentation to discuss options live, 5) Secure Closing via email booking form, 6) Full Trip Support including seats, meals, changes and emergencies until the client returns home.",
        "tunnel": "sales",
        "category_id": "company",
    },
]


def generate_id(title: str) -> str:
    """Deterministic UUID from title — re-running does not create duplicates."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"bbc-faq:{title}"))


def upsert_faq_entries():
    if not QDRANT_URL or not QDRANT_API_KEY:
        print("ERROR: QDRANT_URL and QDRANT_API_KEY must be set in .env")
        return

    client = httpx.Client(
        base_url=QDRANT_URL,
        headers={"api-key": QDRANT_API_KEY},
        timeout=30.0,
    )

    # Build points — text embedding is server-side on Qdrant Cloud
    points = []
    for entry in FAQ_ENTRIES:
        point_id = generate_id(entry["title"])
        # Combine title + content for embedding (same pattern as qdrant.py line 89)
        embed_text = f"{entry['title']} — {entry['content']}"
        points.append({
            "id": point_id,
            "vector": {
                "text": embed_text,
                "model": EMBEDDING_MODEL,
            },
            "payload": {
                "title": entry["title"],
                "content": entry["content"],
                "tunnel": entry["tunnel"],
                "category_id": entry["category_id"],
                "source": "jivo_faq",
            },
        })

    # Upsert in batches of 20
    url = f"/collections/{COLLECTION}/points"
    total = len(points)
    batch_size = 20
    success = 0

    for i in range(0, total, batch_size):
        batch = points[i:i + batch_size]
        resp = client.put(url, json={"points": batch})
        if resp.status_code in (200, 201):
            success += len(batch)
            print(f"  Upserted {success}/{total} entries...")
        else:
            print(f"  ERROR on batch {i}: {resp.status_code} {resp.text}")

    client.close()
    print(f"\nDone. {success}/{total} FAQ entries upserted to '{COLLECTION}'.")
    print(f"Source tag: 'jivo_faq' — use this to filter/delete these entries later.")


def verify_entries():
    """Quick verification — count points in collection."""
    if not QDRANT_URL or not QDRANT_API_KEY:
        return
    client = httpx.Client(
        base_url=QDRANT_URL,
        headers={"api-key": QDRANT_API_KEY},
        timeout=10.0,
    )
    resp = client.get(f"/collections/{COLLECTION}")
    if resp.status_code == 200:
        data = resp.json()
        count = data.get("result", {}).get("points_count", "unknown")
        print(f"Collection '{COLLECTION}' now has {count} total points.")
    client.close()


if __name__ == "__main__":
    print(f"Seeding {len(FAQ_ENTRIES)} FAQ entries into Qdrant...")
    print(f"  URL: {QDRANT_URL}")
    print(f"  Collection: {COLLECTION}")
    print(f"  Model: {EMBEDDING_MODEL}")
    print()
    upsert_faq_entries()
    verify_entries()
