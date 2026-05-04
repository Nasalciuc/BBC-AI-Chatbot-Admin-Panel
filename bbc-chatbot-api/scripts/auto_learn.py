"""
BBC Auto-Learning Pipeline — crawl website, chunk, index in Qdrant.

Usage:
    python scripts/auto_learn.py              # full pipeline
    python scripts/auto_learn.py --dry-run    # crawl + chunk, no upsert
    python scripts/auto_learn.py --verify     # test queries on existing KB
    
Requires: pip install crawl4ai httpx python-dotenv
Requires: crawl4ai-setup (installs Playwright + Chromium)
"""

import asyncio
import argparse
import hashlib
import re
import sys
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
import os

load_dotenv()

# ============================================================
# CONFIG
# ============================================================

QDRANT_URL = os.getenv("QDRANT_URL", "").rstrip("/")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "")
COLLECTION = os.getenv("QDRANT_COLLECTION", "kb_entries")
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

MAIN_SITE_URL = "https://buybusinessclass.com"
BLOG_URL = "https://blog.buybusinessclass.com"

MAIN_MAX_DEPTH = 3
MAIN_MAX_PAGES = 300
BLOG_MAX_DEPTH = 2
BLOG_MAX_PAGES = 50
PRUNING_THRESHOLD = 0.48

CHUNK_MAX_WORDS = 500
CHUNK_OVERLAP_WORDS = 50
CHUNK_MIN_WORDS = 50

SOURCE_TAG = "website_crawl"

# Pages to skip (no useful content for chatbot)
SKIP_PATHS = ["/faq", "/privacy", "/terms", "/sitemap", "/wp-admin", "/cart", "/checkout"]
SKIP_EXACT = ["/airlines", "/best-deals"]

# Boilerplate noise patterns to remove from middle of content
NOISE_PATTERNS = [
    r"Loading\.{2,}",
    r"Round Trip\s*\|?\s*One Way\s*\|?\s*Multi City",
    r"Business Class\s*\|\s*First Class\s*\|\s*Passengers?\s*\d*",
    r"Search Flight",
    r"Adults?\s*\d*\s*Childrens?\s*\d*\s*Infants?\s*\d*",
    r"How does this work\?",
    r"Request a quote[^#]*?Book your flight",
    r"Fill in your information[^#]*?Find the best deal",
]

FOOTER_MARKERS = ["99% of Travelers", "Contact Us", "Contact us", "© 20"]

MIN_EXPECTED_PAGES = 10
MAX_EXPECTED_CHUNKS = 800


# ============================================================
# CRAWL
# ============================================================

async def crawl_website(base_url: str, allowed_domains: list[str],
                        max_depth: int, max_pages: int) -> list[dict]:
    """Crawl a website using Crawl4AI BFS. Return list of page dicts."""
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
    from crawl4ai.content_filter_strategy import PruningContentFilter
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
    from crawl4ai.deep_crawling import BFSDeepCrawlStrategy
    from crawl4ai.deep_crawling.filters import FilterChain, DomainFilter

    browser_config = BrowserConfig(headless=True, verbose=False)
    md_generator = DefaultMarkdownGenerator(
        content_filter=PruningContentFilter(
            threshold=PRUNING_THRESHOLD, threshold_type="fixed"
        )
    )
    crawl_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        markdown_generator=md_generator,
        deep_crawl_strategy=BFSDeepCrawlStrategy(
            max_depth=max_depth,
            max_pages=max_pages,
            include_external=False,
            filter_chain=FilterChain([
                DomainFilter(allowed_domains=allowed_domains),
            ]),
        ),
    )

    pages = []
    async with AsyncWebCrawler(config=browser_config) as crawler:
        results = await crawler.arun(url=base_url, config=crawl_config)

        if not isinstance(results, list):
            results = [results]

        for r in results:
            if not r.success:
                continue
            fit = ""
            if r.markdown:
                fit = r.markdown.fit_markdown or r.markdown.raw_markdown or ""
            if not fit or len(fit.strip()) < 100:
                continue

            title = ""
            if r.metadata and isinstance(r.metadata, dict):
                title = r.metadata.get("title", "") or ""

            pages.append({
                "url": r.url,
                "title": title,
                "markdown": fit,
            })

    return pages


async def crawl_all() -> list[dict]:
    """Crawl main site + blog. Two crawls, reuses browser via sequential calls."""
    print(f"Crawling {MAIN_SITE_URL} (depth={MAIN_MAX_DEPTH}, max={MAIN_MAX_PAGES})...")
    main_pages = await crawl_website(
        base_url=MAIN_SITE_URL,
        allowed_domains=["buybusinessclass.com"],
        max_depth=MAIN_MAX_DEPTH,
        max_pages=MAIN_MAX_PAGES,
    )
    print(f"  Main site: {len(main_pages)} pages with content")

    print(f"Crawling {BLOG_URL} (depth={BLOG_MAX_DEPTH}, max={BLOG_MAX_PAGES})...")
    blog_pages = await crawl_website(
        base_url=BLOG_URL,
        allowed_domains=["blog.buybusinessclass.com"],
        max_depth=BLOG_MAX_DEPTH,
        max_pages=BLOG_MAX_PAGES,
    )
    print(f"  Blog: {len(blog_pages)} pages with content")

    all_pages = main_pages + blog_pages
    print(f"  Total: {len(all_pages)} pages")
    return all_pages


# ============================================================
# BOILERPLATE STRIPPING
# ============================================================

def strip_boilerplate(markdown: str) -> str:
    """Remove nav, footer, and form noise from page markdown."""
    lines = markdown.split("\n")

    # Step 1: Cut everything BEFORE first heading (nav area)
    start = 0
    for i, line in enumerate(lines):
        if line.strip().startswith("#"):
            start = i
            break

    # Step 2: Cut everything AFTER footer markers (search in last 30 lines only)
    end = len(lines)
    search_from = max(start, len(lines) - 30)
    for i in range(len(lines) - 1, search_from - 1, -1):
        if any(marker in lines[i] for marker in FOOTER_MARKERS):
            end = i
            break

    content = "\n".join(lines[start:end])

    # Step 3: Remove form field noise from middle
    for pattern in NOISE_PATTERNS:
        content = re.sub(pattern, "", content, flags=re.DOTALL | re.IGNORECASE)

    # Step 4: Clean multiple blank lines
    content = re.sub(r"\n{3,}", "\n\n", content)
    return content.strip()


# ============================================================
# URL HELPERS
# ============================================================

def should_skip(url: str) -> bool:
    """Check if URL should be excluded from indexing."""
    path = urlparse(url).path.lower().rstrip("/")
    if not path:
        return False  # homepage is OK
    if path in SKIP_EXACT or any(path.startswith(s) for s in SKIP_EXACT):
        return True
    return any(skip in path for skip in SKIP_PATHS)


def classify_url(url: str) -> str:
    """Classify page by URL pattern."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = parsed.path.lower()

    if "blog" in host:
        return "blog"
    if "/flight/city/" in path:
        return "destination_city"
    if "/flight/country/" in path:
        return "destination_country"
    if "/flight/region/" in path:
        return "destination_region"
    if "/about" in path:
        return "company"
    if "/review" in path:
        return "company"
    if "/business-class" in path:
        return "product"
    if path in ("/", ""):
        return "homepage"
    return "general"


def url_to_heading_path(url: str, page_title: str = "") -> str:
    """Convert URL to human-readable heading path for embedding context."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = parsed.path.strip("/")

    # Blog: prefer page title over URL slug
    if "blog" in host:
        if page_title and len(page_title) > 10 and page_title.lower() not in ("blog", "blog - buybusinessclass"):
            return f"Blog > {page_title}"
        if path:
            slug = path.split("/")[-1]
            return f"Blog > {slug.replace('-', ' ').title()}"
        return "Blog"

    if not path:
        return "Home"

    parts = path.split("/")

    # /flight/city/london/23 → "Flights > City > London"
    if parts[0] == "flight" and len(parts) >= 3:
        location = parts[2].replace("-", " ").title()
        category = parts[1].title()
        return f"Flights > {category} > {location}"

    # /best-deals/europe/1 → "Best Deals > Europe"
    if parts[0] == "best-deals" and len(parts) >= 2:
        region = parts[1].replace("-", " ").title()
        return f"Best Deals > {region}"

    # /about-us → "About Us", /business-class-flights → "Business Class Flights"
    return parts[0].replace("-", " ").title()


# ============================================================
# CHUNKING
# ============================================================

def count_words(text: str) -> int:
    return len(text.split())


def chunk_markdown(markdown: str, max_words: int = CHUNK_MAX_WORDS,
                   overlap_words: int = CHUNK_OVERLAP_WORDS,
                   min_words: int = CHUNK_MIN_WORDS) -> list[str]:
    """Split markdown by headings, enforce max length, filter short, dedup, add overlap."""

    # Step 1: Split on headings (keep heading with its section)
    sections = re.split(r"(?=\n##+ )", markdown)

    chunks = []
    for section in sections:
        section = section.strip()
        if not section:
            continue

        if count_words(section) <= max_words:
            chunks.append(section)
        else:
            # Too long — split on paragraph boundaries
            paragraphs = section.split("\n\n")
            current = ""
            for para in paragraphs:
                para = para.strip()
                if not para:
                    continue
                test = (current + "\n\n" + para).strip()
                if count_words(test) > max_words and current:
                    chunks.append(current)
                    current = para
                else:
                    current = test
            if current:
                chunks.append(current)

    # Step 2: Filter short chunks
    chunks = [c for c in chunks if count_words(c) >= min_words]

    # Step 3: Dedup by hash
    seen = set()
    unique = []
    for c in chunks:
        h = hashlib.md5(c.encode()).hexdigest()
        if h not in seen:
            seen.add(h)
            unique.append(c)

    # Step 4: Add overlap
    if overlap_words > 0 and len(unique) > 1:
        overlapped = [unique[0]]
        for i in range(1, len(unique)):
            prev_tail = " ".join(unique[i - 1].split()[-overlap_words:])
            overlapped.append(prev_tail + "\n\n" + unique[i])
        return overlapped

    return unique


# ============================================================
# PROCESS PAGES → CHUNKS
# ============================================================

def process_pages(pages: list[dict]) -> list[dict]:
    """Process all crawled pages: skip → strip → chunk → classify → heading path."""
    all_chunks = []
    now = datetime.now(timezone.utc).isoformat()

    for page in pages:
        url = page["url"]

        # Skip unwanted pages
        if should_skip(url):
            continue

        # Strip boilerplate
        cleaned = strip_boilerplate(page["markdown"])
        if count_words(cleaned) < CHUNK_MIN_WORDS:
            continue

        # Chunk
        text_chunks = chunk_markdown(cleaned)
        if not text_chunks:
            continue

        # Classify + heading path
        category = classify_url(url)
        heading_path = url_to_heading_path(url, page.get("title", ""))

        for idx, chunk_text in enumerate(text_chunks):
            chunk_id = str(uuid.uuid5(
                uuid.NAMESPACE_URL, f"bbc-crawl:{url}:{idx}"
            ))
            all_chunks.append({
                "id": chunk_id,
                "title": heading_path,
                "content": chunk_text,
                "category": category,
                "source_url": url,
                "tunnel": "sales",
                "crawled_at": now,
            })

    return all_chunks


# ============================================================
# QDRANT OPERATIONS
# ============================================================

def get_qdrant_client() -> httpx.Client:
    if not QDRANT_URL or not QDRANT_API_KEY:
        print("ERROR: QDRANT_URL and QDRANT_API_KEY must be set in .env")
        sys.exit(1)
    return httpx.Client(
        base_url=QDRANT_URL,
        headers={"api-key": QDRANT_API_KEY},
        timeout=30.0,
    )


def count_points(client: httpx.Client) -> int:
    resp = client.get(f"/collections/{COLLECTION}")
    if resp.status_code == 200:
        return resp.json().get("result", {}).get("points_count", 0)
    return -1


def ensure_payload_index(client: httpx.Client) -> None:
    """Create keyword index on 'source' field so filter-by-source works."""
    resp = client.put(
        f"/collections/{COLLECTION}/index",
        json={"field_name": "source", "field_schema": "keyword"},
    )
    if resp.status_code in (200, 201):
        print("  Payload index on 'source' confirmed.")
    elif resp.status_code == 400 and "already exists" in resp.text.lower():
        pass  # already indexed — ok
    else:
        print(f"  WARNING: Could not create index: {resp.status_code} {resp.text[:200]}")


def delete_old_crawl_data(client: httpx.Client) -> bool:
    """Delete all points with source=website_crawl."""
    resp = client.post(
        f"/collections/{COLLECTION}/points/delete",
        json={
            "filter": {
                "must": [{"key": "source", "match": {"value": SOURCE_TAG}}]
            }
        },
    )
    if resp.status_code in (200, 201):
        print(f"  Deleted old {SOURCE_TAG} entries.")
        return True
    else:
        print(f"  WARNING: Delete returned {resp.status_code}: {resp.text[:200]}")
        return False


def upsert_chunks(client: httpx.Client, chunks: list[dict],
                  batch_size: int = 20) -> int:
    """Upsert chunks to Qdrant. Server-side embedding."""
    url = f"/collections/{COLLECTION}/points"
    total = len(chunks)
    success = 0

    for i in range(0, total, batch_size):
        batch = chunks[i:i + batch_size]
        points = []
        for ch in batch:
            embed_text = f"{ch['title']} — {ch['content']}"
            points.append({
                "id": ch["id"],
                "vector": {
                    "text": embed_text,
                    "model": EMBEDDING_MODEL,
                },
                "payload": {
                    "title": ch["title"],
                    "content": ch["content"],
                    "tunnel": ch["tunnel"],
                    "category_id": ch["category"],
                    "source": SOURCE_TAG,
                    "source_url": ch["source_url"],
                    "crawled_at": ch["crawled_at"],
                },
            })

        resp = client.put(url, json={"points": points})
        if resp.status_code in (200, 201):
            success += len(batch)
            print(f"  Upserted {success}/{total} chunks...")
        else:
            print(f"  ERROR on batch {i}: {resp.status_code} {resp.text[:200]}")
            print("  Aborting upsert. Partial data may exist. Re-run to retry.")
            return success

    return success


# ============================================================
# VERIFY
# ============================================================

def verify_search(client: httpx.Client) -> bool:
    """Run test queries against website_crawl entries."""
    test_queries = [
        "business class flights to London",
        "how much to fly business class to Tokyo",
        "flights to Spain business class",
        "business class deals to Europe",
        "about buybusinessclass company",
    ]
    print("\nVerification — searching website_crawl entries:")
    all_pass = True

    for query in test_queries:
        body = {
            "query": {"text": query, "model": EMBEDDING_MODEL},
            "limit": 3,
            "score_threshold": 0.55,
            "with_payload": True,
            "filter": {
                "must": [{"key": "source", "match": {"value": SOURCE_TAG}}]
            },
        }
        resp = client.post(
            f"/collections/{COLLECTION}/points/query", json=body
        )
        if resp.status_code == 200:
            points = resp.json().get("result", {}).get("points", [])
            if points:
                top = points[0]
                score = top.get("score", 0)
                title = top.get("payload", {}).get("title", "?")
                print(f"  OK '{query}' -> {title} (score: {score:.3f})")
            else:
                print(f"  FAIL '{query}' -> NO RESULTS")
                all_pass = False
        else:
            print(f"  FAIL '{query}' -> Qdrant error {resp.status_code}")
            all_pass = False

    return all_pass


# ============================================================
# DRY-RUN STATS
# ============================================================

def print_dry_run_stats(pages: list[dict], chunks: list[dict]) -> None:
    print(f"\n{'='*60}")
    print(f"DRY-RUN REPORT")
    print(f"{'='*60}")
    print(f"Pages crawled:  {len(pages)}")
    print(f"Chunks created: {len(chunks)}")

    # Category distribution
    cats = {}
    for ch in chunks:
        cats[ch["category"]] = cats.get(ch["category"], 0) + 1
    print(f"\nCategory distribution:")
    for cat, count in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"  {cat:25s} {count:4d} chunks")

    # Word count stats
    word_counts = [count_words(ch["content"]) for ch in chunks]
    if word_counts:
        print(f"\nChunk sizes (words):")
        print(f"  min={min(word_counts)}, max={max(word_counts)}, "
              f"avg={sum(word_counts)//len(word_counts)}")

    # Sample chunks
    print(f"\n--- Sample chunks (first 3) ---")
    for ch in chunks[:3]:
        print(f"\n  [{ch['category']}] {ch['title']}")
        preview = ch["content"][:200].replace("\n", " ")
        print(f"  {preview}...")

    # Sample heading paths
    print(f"\n--- Sample heading paths ---")
    seen_paths = set()
    for ch in chunks:
        if ch["title"] not in seen_paths and len(seen_paths) < 10:
            seen_paths.add(ch["title"])
            print(f"  {ch['title']}")

    print(f"\n{'='*60}")
    print(f"No data was written to Qdrant (dry-run mode).")
    print(f"Review above, then run without --dry-run to upsert.")
    print(f"{'='*60}")


# ============================================================
# MAIN
# ============================================================

async def main():
    parser = argparse.ArgumentParser(description="BBC Auto-Learning Pipeline")
    parser.add_argument("--dry-run", action="store_true",
                        help="Crawl and chunk but do NOT write to Qdrant")
    parser.add_argument("--verify", action="store_true",
                        help="Only run test queries on existing KB")
    args = parser.parse_args()

    # --- Verify-only mode ---
    if args.verify:
        client = get_qdrant_client()
        before = count_points(client)
        print(f"Collection '{COLLECTION}' has {before} points.")
        ensure_payload_index(client)
        ok = verify_search(client)
        client.close()
        sys.exit(0 if ok else 1)

    # --- Crawl ---
    print(f"\n{'='*60}")
    print(f"BBC Auto-Learning Pipeline")
    print(f"{'='*60}\n")

    pages = await crawl_all()

    if len(pages) == 0:
        print("ERROR: Crawl returned 0 pages. Site may be down or blocking.")
        print("Existing KB unchanged. Exiting.")
        sys.exit(1)

    if len(pages) < MIN_EXPECTED_PAGES:
        print(f"WARNING: Only {len(pages)} pages (expected >{MIN_EXPECTED_PAGES}).")
        print("Site structure may have changed. Run with --dry-run to inspect.")
        if not args.dry_run:
            print("Aborting upsert. Use --dry-run first.")
            sys.exit(1)

    # --- Process ---
    print(f"\nProcessing {len(pages)} pages...")
    chunks = process_pages(pages)
    print(f"  Generated {len(chunks)} chunks")

    if len(chunks) == 0:
        print("ERROR: 0 chunks generated. Content may be empty or all filtered.")
        sys.exit(1)

    if len(chunks) > MAX_EXPECTED_CHUNKS:
        print(f"WARNING: {len(chunks)} chunks (expected <{MAX_EXPECTED_CHUNKS}).")
        print("Blog or content may need tighter filtering.")

    # --- Dry-run ---
    if args.dry_run:
        print_dry_run_stats(pages, chunks)
        return

    # --- Upsert ---
    client = get_qdrant_client()
    before = count_points(client)
    print(f"\nQdrant before: {before} points in '{COLLECTION}'")

    print("Ensuring payload index on 'source' field...")
    ensure_payload_index(client)
    print("Deleting old website_crawl entries...")
    delete_old_crawl_data(client)

    print(f"Upserting {len(chunks)} new chunks...")
    upserted = upsert_chunks(client, chunks)

    after = count_points(client)
    print(f"\nQdrant after: {after} points in '{COLLECTION}'")
    print(f"  Existing (non-crawl): ~{before} → still intact")
    print(f"  Website crawl: {upserted} new chunks added")

    # --- Verify ---
    ok = verify_search(client)
    client.close()

    # --- Summary ---
    print(f"\n{'='*60}")
    print(f"DONE — Auto-learning pipeline complete")
    print(f"  Pages crawled: {len(pages)}")
    print(f"  Chunks indexed: {upserted}")
    print(f"  Total KB points: {after}")
    print(f"  Verification: {'ALL PASS' if ok else 'SOME FAILED'}")
    print(f"  Source tag: '{SOURCE_TAG}'")
    print(f"{'='*60}")

    if not ok:
        print("\nWARNING: Some test queries failed. Review content quality.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
