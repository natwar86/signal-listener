"""
Shopify App Store review collector.

Adapted from V0.1 scraper.py. Key changes for V2:
- Returns Signal objects instead of raw dicts
- Supports incremental scraping (only new reviews)
- Integrates with the SQLite DB for deduplication
"""

import re
import json
import logging
import unicodedata
from pathlib import Path
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from collectors.base import PoliteFetcher
from processor.schema import Signal, Author, Content
from db import insert_signal, get_connection
from config import OUTPUT_DIR

log = logging.getLogger("signal-listener")

BASE_URL = "https://apps.shopify.com"


# ---------------------------------------------------------------------------
# HTML parsing (carried over from V0.1)
# ---------------------------------------------------------------------------

def parse_reviews_page(html: str) -> list[dict]:
    """Parse a single Shopify reviews page into raw review dicts."""
    soup = BeautifulSoup(html, "lxml")
    reviews = []

    review_blocks = soup.select("[data-merchant-review]")

    for block in review_blocks:
        review = {}

        review["review_id"] = block.get("data-review-content-id", "")

        rating_el = block.select_one('div[role="img"][aria-label*="star"]')
        if rating_el:
            m = re.search(r"([\d.]+)\s+out\s+of\s+5", rating_el["aria-label"])
            if m:
                review["rating"] = float(m.group(1))

        date_el = block.select_one(".tw-text-body-xs.tw-text-fg-tertiary")
        if date_el:
            raw = date_el.get_text(strip=True)
            review["date"] = re.sub(r"^Edited\s+", "", raw)

        review_text_div = block.select_one(
            "[data-truncate-review]:not([data-reply-id]) [data-truncate-content-copy]"
        )
        if review_text_div:
            paragraphs = review_text_div.find_all("p")
            if paragraphs:
                review["body"] = "\n".join(p.get_text(strip=True) for p in paragraphs)
            else:
                review["body"] = review_text_div.get_text(strip=True)
        else:
            review["body"] = ""

        name_el = block.select_one(".tw-text-heading-xs span[title]")
        if name_el:
            review["reviewer"] = name_el["title"]
        else:
            name_el = block.select_one(".tw-text-heading-xs")
            review["reviewer"] = name_el.get_text(strip=True) if name_el else ""

        info_parent = block.select_one("[class*='tw-order-1'][class*='tw-row-span']")
        review["location"] = ""
        review["usage_duration"] = ""
        if info_parent:
            for child_div in info_parent.find_all("div", recursive=False):
                text = child_div.get_text(strip=True)
                if not text:
                    continue
                lower = text.lower()
                if "using the app" in lower or "on the app" in lower:
                    review["usage_duration"] = text
                elif "tw-text-heading" not in " ".join(child_div.get("class", [])):
                    if not review["location"] and len(text) < 80:
                        review["location"] = text

        link_btn = block.select_one("[data-review-share-link]")
        if link_btn:
            review["review_link"] = link_btn.get("data-review-share-link", "")

        if review.get("body"):
            reviews.append(review)

    return reviews


def get_total_pages(html: str) -> int:
    """Extract the last page number from pagination."""
    soup = BeautifulSoup(html, "lxml")
    page_nums = []

    for a in soup.find_all("a", href=True):
        if "page=" in a["href"]:
            try:
                num = int(a["href"].split("page=")[-1].split("&")[0])
                page_nums.append(num)
            except ValueError:
                continue

    for el in soup.find_all(attrs={"aria-label": True}):
        label = el.get("aria-label", "")
        if label.lower().startswith("page "):
            try:
                page_nums.append(int(label.split()[-1]))
            except ValueError:
                continue

    return max(page_nums) if page_nums else 1


# ---------------------------------------------------------------------------
# Store URL resolution (carried over from V0.1)
# ---------------------------------------------------------------------------

def slugify_name(name: str) -> list[str]:
    """Generate candidate myshopify subdomains from a reviewer name."""
    if not name:
        return []

    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = name.lower().strip()
    stripped = re.sub(r"\b(inc|llc|ltd|co|corp|company|store|shop)\.?\s*$", "", name).strip()

    def make_slug(s: str) -> str:
        s = re.sub(r"[^a-z0-9\s-]", "", s)
        s = re.sub(r"[\s-]+", "-", s).strip("-")
        return s

    candidates = []
    slug1 = make_slug(name)
    if slug1:
        candidates.append(slug1)

    slug2 = make_slug(stripped)
    if slug2 and slug2 != slug1:
        candidates.append(slug2)

    no_hyphen = slug1.replace("-", "")
    if no_hyphen and no_hyphen != slug1:
        candidates.append(no_hyphen)

    if slug1.startswith("the-"):
        candidates.append(slug1[4:])

    return candidates


def resolve_store_url(reviewer_name: str, fetcher: PoliteFetcher) -> str:
    """Try to find a reviewer's store URL via myshopify.com subdomain probing."""
    candidates = slugify_name(reviewer_name)
    if not candidates:
        return ""

    for slug in candidates:
        url = f"https://{slug}.myshopify.com"
        resp = fetcher.head(url)
        if resp is None:
            continue

        final_url = resp.url
        store_url = ""
        for hist_resp in resp.history:
            loc = hist_resp.headers.get("Location", "")
            if loc and "myshopify.com" not in loc:
                store_url = loc
                break

        if not store_url and "myshopify.com" not in final_url:
            store_url = final_url

        if store_url:
            store_url = re.sub(r"/password/?$", "", store_url).rstrip("/")
            if store_url.startswith("http://"):
                store_url = "https://" + store_url[7:]
            return store_url

    return ""


# ---------------------------------------------------------------------------
# Convert raw review → Signal
# ---------------------------------------------------------------------------

def parse_date_to_iso(date_str: str) -> str:
    """Convert 'April 23, 2025' to ISO 8601."""
    try:
        dt = datetime.strptime(date_str, "%B %d, %Y")
        return dt.replace(tzinfo=timezone.utc).isoformat()
    except (ValueError, TypeError):
        return ""


def review_to_signal(review: dict, app_slug: str) -> Signal:
    """Convert a raw Shopify review dict into a Signal object."""
    review_id = review.get("review_id", "")
    signal_id = f"shopify_{app_slug}_{review_id}" if review_id else ""

    review_link = review.get("review_link", "")
    source_url = f"{BASE_URL}{review_link}" if review_link else f"{BASE_URL}/{app_slug}/reviews"

    return Signal(
        id=signal_id,
        source="shopify_reviews",
        source_url=source_url,
        timestamp=parse_date_to_iso(review.get("date", "")),
        author=Author(
            name=review.get("reviewer"),
            company=review.get("reviewer"),  # reviewer names are store/business names
            company_url=review.get("store_url", ""),
        ),
        content=Content(
            body=review.get("body", ""),
            rating=review.get("rating"),
        ),
        metadata={
            "app_slug": app_slug,
            "app_url": f"{BASE_URL}/{app_slug}",
            "location": review.get("location", ""),
            "usage_duration": review.get("usage_duration", ""),
            "review_link": review.get("review_link", ""),
        },
    )


# ---------------------------------------------------------------------------
# Main collector
# ---------------------------------------------------------------------------

def collect_shopify_reviews(
    app_slug: str,
    fetcher: PoliteFetcher,
    max_pages: int | None = None,
    save_html: bool = False,
    skip_resolve: bool = False,
    db_path=None,
) -> list[Signal]:
    """
    Scrape reviews for one app and insert into the DB.
    Returns list of newly inserted signals.

    Always starts from page 1 (newest reviews) and stops when it hits
    pages with no new reviews — so daily runs only fetch what's new.
    On first run (no existing data), scrapes all available pages.
    """
    from config import DB_PATH
    if db_path is None:
        db_path = DB_PATH

    app_url = f"{BASE_URL}/{app_slug}"
    app_dir = OUTPUT_DIR / app_slug
    app_dir.mkdir(parents=True, exist_ok=True)

    # Check if we already have data for this app (incremental mode)
    conn = get_connection(db_path)
    existing_count = conn.execute(
        "SELECT COUNT(*) FROM signals WHERE raw_json LIKE ?",
        (f'%"app_slug": "{app_slug}"%',),
    ).fetchone()[0]
    conn.close()
    is_incremental = existing_count > 0

    # Fetch page 1 to determine total pages
    log.info(f"[{app_slug}] Fetching page 1 (incremental={is_incremental}, existing={existing_count})...")
    resp = fetcher.fetch(f"{app_url}/reviews?page=1")
    if resp is None:
        log.error(f"[{app_slug}] Could not reach reviews page")
        return []

    total_pages = get_total_pages(resp.text)

    if save_html:
        _save_debug_html(resp.text, app_slug, 1)

    # Process page 1
    reviews = parse_reviews_page(resp.text)
    new_signals = _process_page_reviews(reviews, app_slug, fetcher, skip_resolve, db_path)
    log.info(f"[{app_slug}] Page 1/{total_pages}: {len(reviews)} reviews, {len(new_signals)} new")

    # In incremental mode, stop early if page 1 had no new reviews
    if is_incremental and len(new_signals) == 0:
        log.info(f"[{app_slug}] No new reviews on page 1 — already up to date.")
        return new_signals

    # Determine how many pages to scrape
    if max_pages is not None:
        total_pages = min(total_pages, max_pages)

    log.info(f"[{app_slug}] Scraping pages 2-{total_pages}")

    no_new_streak = 0
    empty_streak = 0
    for page in range(2, total_pages + 1):
        url = f"{app_url}/reviews?page={page}"
        resp = fetcher.fetch(url)
        if resp is None:
            log.warning(f"[{app_slug}] Skipping page {page} (fetch failed)")
            continue

        if save_html:
            _save_debug_html(resp.text, app_slug, page)

        reviews = parse_reviews_page(resp.text)
        if reviews:
            signals = _process_page_reviews(reviews, app_slug, fetcher, skip_resolve, db_path)
            new_signals.extend(signals)
            empty_streak = 0
            log.info(f"[{app_slug}] Page {page}/{total_pages}: {len(reviews)} reviews, {len(signals)} new")

            # In incremental mode, stop if we've hit all-duplicate pages
            if is_incremental and len(signals) == 0:
                no_new_streak += 1
                if no_new_streak >= 2:
                    log.info(f"[{app_slug}] 2 pages with no new reviews — caught up.")
                    break
            else:
                no_new_streak = 0
        else:
            empty_streak += 1
            log.warning(f"[{app_slug}] Page {page}: 0 reviews parsed")
            if empty_streak >= 3:
                log.warning(f"[{app_slug}] 3 empty pages in a row — stopping")
                break

    log.info(f"[{app_slug}] Done. {len(new_signals)} new signals inserted.")
    return new_signals


def _process_page_reviews(
    reviews: list[dict],
    app_slug: str,
    fetcher: PoliteFetcher,
    skip_resolve: bool,
    db_path,
) -> list[Signal]:
    """Convert raw reviews to signals and insert into DB."""
    # Load store URL cache
    cache_file = OUTPUT_DIR / app_slug / "store_url_cache.json"
    cache = {}
    if cache_file.exists():
        with open(cache_file) as f:
            cache = json.load(f)

    signals = []
    for review in reviews:
        # Resolve store URL if not cached
        reviewer = review.get("reviewer", "")
        if not skip_resolve and reviewer and reviewer not in cache:
            store_url = resolve_store_url(reviewer, fetcher)
            cache[reviewer] = store_url
            review["store_url"] = store_url
        else:
            review["store_url"] = cache.get(reviewer, "")

        signal = review_to_signal(review, app_slug)
        inserted = insert_signal(signal, db_path)
        if inserted:
            signals.append(signal)

    # Save cache
    if not skip_resolve:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_file, "w") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)

    return signals


def _save_progress(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _save_debug_html(html: str, app_slug: str, page: int):
    debug_dir = OUTPUT_DIR / app_slug / "debug_html"
    debug_dir.mkdir(parents=True, exist_ok=True)
    with open(debug_dir / f"page_{page}.html", "w") as f:
        f.write(html)
