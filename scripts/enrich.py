#!/usr/bin/env python3
"""
Enrich signals with company websites and email addresses.

Strategy (optimized for speed):
  1. Exa API search first (fast, ~90% hit rate)
  2. Falls back to myshopify.com probing + direct domain probing
  3. Scrapes homepage + /contact for email addresses
  4. Runs with parallel workers for throughput

Usage:
    python -m scripts.enrich                # Enrich up to 50 signals
    python -m scripts.enrich --limit 200    # Enrich more
    python -m scripts.enrich --dry-run      # Preview what would be enriched
    python -m scripts.enrich --emails-only  # Only find emails for already-resolved URLs
    python -m scripts.enrich --workers 10   # Set parallelism (default: 5)
"""

import re
import sys
import json
import logging
import argparse
import unicodedata
from pathlib import Path
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import get_connection, init_db
from config import DB_PATH, EXA_API_KEY
from collectors.base import PoliteFetcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("signal-listener")

# Common email patterns on websites
EMAIL_REGEX = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
)

# Emails to skip
SKIP_EMAIL_DOMAINS = {
    "example.com", "sentry.io", "wixpress.com", "shopify.com",
    "cloudflare.com", "googleapis.com", "google.com", "facebook.com",
    "twitter.com", "schema.org", "w3.org", "jquery.com",
    "gravatar.com", "wordpress.org", "wordpress.com", "myshopify.com",
}
SKIP_EMAIL_PREFIXES = {"noreply", "no-reply", "mailer-daemon", "postmaster"}

# Domains that are known parked/spam redirects — not real stores
SPAM_DOMAINS = {
    "pippalee.com", "sedoparking.com", "hugedomains.com",
    "dan.com", "afternic.com", "godaddy.com", "domainempire.com",
    "bodis.com", "parkingcrew.net", "above.com", "sedo.com",
}

SKIP_MARKETPLACE_DOMAINS = {
    "amazon.com", "etsy.com", "ebay.com", "apps.shopify.com",
    "yelp.com", "facebook.com", "instagram.com",
    "linkedin.com", "twitter.com", "youtube.com",
    "tiktok.com", "pinterest.com", "reddit.com",
    "wikipedia.org", "crunchbase.com", "bloomberg.com",
}

# Placeholder emails found in site templates
SKIP_EMAILS = {
    "your@email.com", "email@example.com", "name@example.com",
    "info@example.com", "test@test.com", "admin@example.com",
}

# Thread-safe DB lock
_db_lock = threading.Lock()


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_unresolved_signals(limit: int = 50, db_path=None):
    """Get signals that don't have a company URL."""
    conn = get_connection(db_path or DB_PATH)
    rows = conn.execute("""
        SELECT id, author_name, author_company, author_company_url, raw_json
        FROM signals
        WHERE (author_company_url IS NULL OR author_company_url = '')
          AND author_name IS NOT NULL
          AND author_name != ''
          AND author_name != 'My Store'
        ORDER BY
            CASE WHEN urgency = 'hot' THEN 0
                 WHEN urgency = 'warm' THEN 1
                 ELSE 2 END,
            timestamp DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_signals_missing_email(limit: int = 50, db_path=None):
    """Get signals that have a URL but no email."""
    conn = get_connection(db_path or DB_PATH)
    rows = conn.execute("""
        SELECT id, author_name, author_company_url, raw_json
        FROM signals
        WHERE author_company_url IS NOT NULL
          AND author_company_url != ''
          AND (raw_json NOT LIKE '%"email"%'
               OR raw_json LIKE '%"email": ""%'
               OR raw_json LIKE '%"email": null%')
        ORDER BY
            CASE WHEN urgency = 'hot' THEN 0
                 WHEN urgency = 'warm' THEN 1
                 ELSE 2 END,
            timestamp DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_company_url(signal_id: str, url: str, db_path=None):
    """Update the company URL for a signal."""
    with _db_lock:
        conn = get_connection(db_path or DB_PATH)
        conn.execute(
            "UPDATE signals SET author_company_url = ? WHERE id = ?",
            (url, signal_id),
        )
        row = conn.execute(
            "SELECT raw_json FROM signals WHERE id = ?", (signal_id,)
        ).fetchone()
        if row:
            d = json.loads(row["raw_json"])
            if "author" in d:
                d["author"]["company_url"] = url
            conn.execute(
                "UPDATE signals SET raw_json = ? WHERE id = ?",
                (json.dumps(d), signal_id),
            )
        conn.commit()
        conn.close()


def update_email(signal_id: str, email: str, db_path=None):
    """Store a discovered email in the signal's raw_json metadata."""
    with _db_lock:
        conn = get_connection(db_path or DB_PATH)
        row = conn.execute(
            "SELECT raw_json FROM signals WHERE id = ?", (signal_id,)
        ).fetchone()
        if row:
            d = json.loads(row["raw_json"])
            if "metadata" not in d:
                d["metadata"] = {}
            d["metadata"]["email"] = email
            conn.execute(
                "UPDATE signals SET raw_json = ? WHERE id = ?",
                (json.dumps(d), signal_id),
            )
        conn.commit()
        conn.close()


# ---------------------------------------------------------------------------
# URL Resolution: Exa API (primary)
# ---------------------------------------------------------------------------

def try_exa_search(name: str) -> str:
    """Use Exa API to find a company website. Fast and high hit rate."""
    if not EXA_API_KEY:
        return ""

    import requests as _requests

    if len(name.strip()) < 3:
        return ""

    query = f'"{name}" official website'
    try:
        resp = _requests.post(
            "https://api.exa.ai/search",
            headers={"x-api-key": EXA_API_KEY, "Content-Type": "application/json"},
            json={
                "query": query,
                "type": "auto",
                "numResults": 3,
                "category": "company",
            },
            timeout=15,
        )
        if resp.status_code != 200:
            log.warning(f"  Exa API error: {resp.status_code}")
            return ""

        results = resp.json().get("results", [])
        if not results:
            return ""

        name_words = set(re.sub(r"[^a-z0-9\s]", "", name.lower()).split())

        # First pass: prefer URLs where domain shares words with name
        for r in results:
            url = r.get("url", "")
            if not url:
                continue
            parsed = urlparse(url)
            domain = parsed.netloc.lower().lstrip("www.")

            if any(spam in domain for spam in SPAM_DOMAINS):
                continue
            if any(m in domain for m in SKIP_MARKETPLACE_DOMAINS):
                continue

            domain_root = domain.split(".")[0]
            if any(w in domain_root for w in name_words if len(w) >= 3):
                return f"https://{domain}"

        # No word-match found — skip to avoid false positives

    except Exception as e:
        log.warning(f"  Exa search failed: {e}")

    return ""


# ---------------------------------------------------------------------------
# URL Resolution: myshopify.com probing (fallback)
# ---------------------------------------------------------------------------

def generate_myshopify_slugs(name: str) -> list[str]:
    """Generate candidate myshopify subdomains from a reviewer name."""
    if not name:
        return []

    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = name.lower().strip()
    stripped = re.sub(
        r"\b(inc|llc|ltd|co|corp|company|store|shop|usa|us|uk|au|ca|"
        r"official|online|the|wholesale|retail|collection)\b\.?\s*",
        "", name,
    ).strip()

    def make_slug(s: str) -> str:
        s = re.sub(r"[^a-z0-9\s-]", "", s)
        s = re.sub(r"[\s-]+", "-", s).strip("-")
        return s

    candidates = set()
    for variant in [name, stripped]:
        slug = make_slug(variant)
        if slug and len(slug) >= 3:
            candidates.add(slug)
            candidates.add(slug.replace("-", ""))
            if slug.startswith("the-"):
                candidates.add(slug[4:])
                candidates.add(slug[4:].replace("-", ""))

    # DNS labels can't exceed 63 chars
    return [c for c in candidates if 3 <= len(c) <= 60][:6]


def try_myshopify(name: str, fetcher: PoliteFetcher) -> str:
    """Try to resolve via myshopify.com subdomain probing."""
    for slug in generate_myshopify_slugs(name):
        resp = fetcher.head(f"https://{slug}.myshopify.com")
        if resp is None:
            continue

        store_url = ""
        for hist_resp in resp.history:
            loc = hist_resp.headers.get("Location", "")
            if loc and "myshopify.com" not in loc:
                store_url = loc
                break

        if not store_url and "myshopify.com" not in resp.url:
            store_url = resp.url

        if store_url:
            store_url = re.sub(r"/password/?$", "", store_url).rstrip("/")
            if store_url.startswith("http://"):
                store_url = "https://" + store_url[7:]
            return store_url

    return ""


# ---------------------------------------------------------------------------
# URL Resolution: Direct domain probing (fallback)
# ---------------------------------------------------------------------------

def generate_domain_candidates(name: str) -> list[str]:
    """Generate candidate domains from a company name."""
    if not name:
        return []

    norm = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    norm = norm.lower().strip()

    cleaned = re.sub(
        r"\b(inc|llc|ltd|co|corp|company|store|shop|usa|us|uk|au|ca|"
        r"official|online|the|wholesale|retail)\b\.?\s*",
        "", norm,
    ).strip()

    def slugify(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", s)

    def hyphenate(s: str) -> str:
        s = re.sub(r"[^a-z0-9\s]", "", s)
        return re.sub(r"\s+", "-", s).strip("-")

    slug = slugify(cleaned)
    slug_full = slugify(norm)
    hyphen = hyphenate(cleaned)

    if not slug or len(slug) < 3:
        return []

    domains = set()
    for s in [slug, slug_full]:
        if len(s) >= 3:
            domains.add(f"{s}.com")
            domains.add(f"shop{s}.com")
            domains.add(f"get{s}.com")
            domains.add(f"the{s}.com")
            domains.add(f"{s}.co")

    if hyphen and hyphen != slug and "-" in hyphen:
        domains.add(f"{hyphen}.com")

    if "&" in name:
        and_version = slugify(norm.replace("&", "and"))
        if and_version and and_version != slug:
            domains.add(f"{and_version}.com")

    words = re.sub(r"[^a-z0-9\s]", "", cleaned).split()
    if len(words) >= 3:
        short = slugify(" ".join(words[:2]))
        if short and short != slug:
            domains.add(f"{short}.com")

    if re.search(r"\.(com|co|net|org|io|us|uk|store|shop)\b", norm):
        domain = re.sub(r"\s+", "", norm)
        domains.add(domain)

    # DNS labels can't exceed 63 chars
    return [d for d in list(domains) if len(d.split(".")[0]) <= 60][:12]


def validate_url(url: str, company_name: str, original_domain: str = "") -> bool:
    """Check if a resolved URL looks legitimate (not parked/spam)."""
    parsed = urlparse(url)
    final_domain = parsed.netloc.lower().lstrip("www.")

    for spam in SPAM_DOMAINS:
        if spam in final_domain:
            return False

    if original_domain:
        orig_root = original_domain.lstrip("www.").split(".")[0]
        final_root = final_domain.split(".")[0]
        if len(orig_root) >= 4 and orig_root not in final_root and final_root not in orig_root:
            return False

    return True


def try_direct_domains(name: str, fetcher: PoliteFetcher) -> str:
    """Try to find a company website by probing common domain patterns."""
    candidates = generate_domain_candidates(name)
    if not candidates:
        return ""

    for domain in candidates:
        url = f"https://{domain}"
        resp = fetcher.head(url)
        if resp is None:
            continue

        final_url = resp.url.rstrip("/")
        if resp.status_code < 400 and validate_url(final_url, name, original_domain=domain):
            if final_url.startswith("http://"):
                final_url = "https://" + final_url[7:]
            final_url = re.sub(r"\?.*$", "", final_url).rstrip("/")
            return final_url

    return ""


# ---------------------------------------------------------------------------
# Email discovery
# ---------------------------------------------------------------------------

def find_emails_on_site(url: str, fetcher: PoliteFetcher) -> list[str]:
    """Scrape homepage + /contact for email addresses."""
    if not url:
        return []

    emails = set()

    # Only check 2 pages — homepage and contact
    pages_to_check = [
        url,
        f"{url}/pages/contact",
    ]

    for page_url in pages_to_check:
        resp = fetcher.fetch(page_url, max_retries=1)
        if resp is None:
            continue

        found = EMAIL_REGEX.findall(resp.text)
        for email in found:
            email = email.lower().strip()
            email_domain = email.split("@")[1] if "@" in email else ""
            email_prefix = email.split("@")[0] if "@" in email else ""

            if email_domain in SKIP_EMAIL_DOMAINS:
                continue
            if email_prefix in SKIP_EMAIL_PREFIXES:
                continue
            if email.endswith((".png", ".jpg", ".svg", ".gif")):
                continue
            if any(x in email for x in ["{", "}", "//", "/*", "webpack", "woff"]):
                continue
            if email in SKIP_EMAILS:
                continue

            emails.add(email)

        if emails:
            break

    return sorted(emails)


def pick_best_email(emails: list[str], domain: str) -> str:
    """Pick the most useful email from a list."""
    if not emails:
        return ""

    domain_root = ".".join(domain.split(".")[-2:]).lower()

    domain_emails = [e for e in emails if domain_root in e]
    other_emails = [e for e in emails if domain_root not in e]

    priority_prefixes = [
        "hello", "hi", "info", "contact", "support", "help",
        "team", "sales", "admin",
    ]

    for prefix in priority_prefixes:
        for e in domain_emails:
            if e.startswith(f"{prefix}@"):
                return e

    if domain_emails:
        return domain_emails[0]
    if other_emails:
        return other_emails[0]

    return emails[0]


# ---------------------------------------------------------------------------
# Worker: enrich a single signal
# ---------------------------------------------------------------------------

def enrich_one(signal: dict, index: int, total: int) -> dict:
    """Enrich a single signal. Returns result dict."""
    name = signal["author_name"]
    result = {"id": signal["id"], "name": name, "url": "", "email": "", "source": ""}

    try:
        # Step 1: Exa API (fast, high hit rate)
        url = try_exa_search(name)
        source = "exa"

        # Step 2: myshopify probing (fallback)
        if not url:
            fetcher = PoliteFetcher(min_delay=0.5, max_delay=1.0)
            try:
                url = try_myshopify(name, fetcher)
                source = "myshopify"

                # Step 3: direct domain probing (fallback)
                if not url:
                    url = try_direct_domains(name, fetcher)
                    source = "domain"
            finally:
                fetcher.close()

        if url:
            log.info(f"[{index}/{total}] {name} -> {url} ({source})")
            update_company_url(signal["id"], url, DB_PATH)
            result["url"] = url
            result["source"] = source

            # Find email
            fetcher = PoliteFetcher(min_delay=0.5, max_delay=1.0)
            try:
                emails = find_emails_on_site(url, fetcher)
                if emails:
                    domain = re.sub(r"^https?://", "", url).split("/")[0]
                    best = pick_best_email(emails, domain)
                    log.info(f"  [{index}] Email: {best}")
                    update_email(signal["id"], best, DB_PATH)
                    result["email"] = best
            finally:
                fetcher.close()
        else:
            log.info(f"[{index}/{total}] {name} -> not found")

    except Exception as e:
        log.warning(f"[{index}/{total}] Error enriching {name}: {e}")

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Enrich signals with URLs and emails")
    parser.add_argument("--limit", type=int, default=50, help="Max signals to process")
    parser.add_argument("--workers", type=int, default=5, help="Parallel workers (default: 5)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without changes")
    parser.add_argument("--emails-only", action="store_true",
                        help="Only find emails for already-resolved URLs")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    init_db(DB_PATH)

    if args.emails_only:
        enrich_emails(args)
    else:
        enrich_urls_and_emails(args)


def enrich_urls_and_emails(args):
    """Find website URLs (and emails) for unresolved signals."""
    signals = get_unresolved_signals(limit=args.limit, db_path=DB_PATH)
    log.info(f"Found {len(signals)} signals without company URL (workers={args.workers})")

    if not signals:
        return

    if args.dry_run:
        for s in signals[:20]:
            log.info(f"  Would enrich: {s['author_name']}")
        if len(signals) > 20:
            log.info(f"  ... and {len(signals) - 20} more")
        return

    resolved = 0
    emails_found = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(enrich_one, sig, i, len(signals)): sig
            for i, sig in enumerate(signals, 1)
        }

        try:
            for future in as_completed(futures):
                result = future.result()
                if result["url"]:
                    resolved += 1
                if result["email"]:
                    emails_found += 1
        except KeyboardInterrupt:
            log.info("\nInterrupted — progress saved. Cancelling pending tasks...")
            pool.shutdown(wait=False, cancel_futures=True)

    log.info(f"Done. Resolved {resolved}/{len(signals)} URLs, found {emails_found} emails.")


def enrich_emails(args):
    """Find emails for signals that already have a company URL."""
    signals = get_signals_missing_email(limit=args.limit, db_path=DB_PATH)
    log.info(f"Found {len(signals)} signals with URL but no email (workers={args.workers})")

    if not signals:
        return

    if args.dry_run:
        for s in signals[:20]:
            log.info(f"  Would check: {s['author_name']} -> {s['author_company_url']}")
        if len(signals) > 20:
            log.info(f"  ... and {len(signals) - 20} more")
        return

    def find_email_one(signal, index, total):
        name = signal["author_name"]
        url = signal["author_company_url"]
        fetcher = PoliteFetcher(min_delay=0.5, max_delay=1.0)
        try:
            emails = find_emails_on_site(url, fetcher)
            if emails:
                domain = re.sub(r"^https?://", "", url).split("/")[0]
                best = pick_best_email(emails, domain)
                log.info(f"[{index}/{total}] {name}: {best}")
                update_email(signal["id"], best, DB_PATH)
                return best
            else:
                log.info(f"[{index}/{total}] {name}: no email")
                return ""
        except Exception as e:
            log.warning(f"[{index}/{total}] Error for {name}: {e}")
            return ""
        finally:
            fetcher.close()

    found = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(find_email_one, sig, i, len(signals)): sig
            for i, sig in enumerate(signals, 1)
        }

        try:
            for future in as_completed(futures):
                if future.result():
                    found += 1
        except KeyboardInterrupt:
            log.info("\nInterrupted — progress saved. Cancelling pending tasks...")
            pool.shutdown(wait=False, cancel_futures=True)

    log.info(f"Done. Found emails for {found}/{len(signals)} signals.")


if __name__ == "__main__":
    main()
