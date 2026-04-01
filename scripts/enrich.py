#!/usr/bin/env python3
"""
Enrich signals with company websites and email addresses.

For reviewers missing a company URL, this script:
  1. Tries myshopify.com subdomain probing (original method)
  2. Tries direct domain probing (name.com, getname.com, etc.)
  3. Scrapes the found website for email addresses

Usage:
    python -m scripts.enrich                # Enrich up to 50 signals
    python -m scripts.enrich --limit 200    # Enrich more
    python -m scripts.enrich --dry-run      # Preview what would be enriched
    python -m scripts.enrich --emails-only  # Only find emails for already-resolved URLs
"""

import re
import sys
import json
import logging
import argparse
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import get_connection, init_db
from config import DB_PATH
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

# Placeholder emails found in site templates
SKIP_EMAILS = {
    "your@email.com", "email@example.com", "name@example.com",
    "info@example.com", "test@test.com", "admin@example.com",
}


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
# Step 1: myshopify.com subdomain probing
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

    return [c for c in candidates if len(c) >= 3][:6]


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
# Step 2: Direct domain probing
# ---------------------------------------------------------------------------

def generate_domain_candidates(name: str) -> list[str]:
    """Generate candidate domains from a company name."""
    if not name:
        return []

    norm = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    norm = norm.lower().strip()

    # Remove common business suffixes
    cleaned = re.sub(
        r"\b(inc|llc|ltd|co|corp|company|store|shop|usa|us|uk|au|ca|"
        r"official|online|the|wholesale|retail)\b\.?\s*",
        "", norm,
    ).strip()

    # Make a slug (no spaces, no special chars)
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

    candidates = []

    # Most common patterns for Shopify stores
    domains = set()
    for s in [slug, slug_full]:
        if len(s) >= 3:
            domains.add(f"{s}.com")
            domains.add(f"www.{s}.com")
            domains.add(f"shop{s}.com")
            domains.add(f"get{s}.com")
            domains.add(f"the{s}.com")
            domains.add(f"{s}.co")

    # Also try hyphenated version
    if hyphen and hyphen != slug and "-" in hyphen:
        domains.add(f"{hyphen}.com")

    # Handle "&" -> "and"
    if "&" in name:
        and_version = slugify(norm.replace("&", "and"))
        if and_version and and_version != slug:
            domains.add(f"{and_version}.com")
            domains.add(f"www.{and_version}.com")

    # If name looks like "Word Word", try dropping last word
    # (e.g., "Frost Buddy" -> frostbuddy.com, already covered)
    # But "Little Bipsy Collection" -> littlebipsy.com
    words = re.sub(r"[^a-z0-9\s]", "", cleaned).split()
    if len(words) >= 3:
        short = slugify(" ".join(words[:2]))
        if short and short != slug:
            domains.add(f"{short}.com")

    # If name already looks like a domain (has .com, .co, etc.)
    if re.search(r"\.(com|co|net|org|io|us|uk|store|shop)\b", norm):
        domain = re.sub(r"\s+", "", norm)
        domains.add(domain)
        domains.add(f"www.{domain}")

    return list(domains)[:12]


def validate_url(url: str, company_name: str, original_domain: str = "") -> bool:
    """Check if a resolved URL looks legitimate (not parked/spam)."""
    parsed = urlparse(url)
    final_domain = parsed.netloc.lower().lstrip("www.")

    # Check against known spam domains
    for spam in SPAM_DOMAINS:
        if spam in final_domain:
            return False

    # If the final domain is completely different from what we requested,
    # it's likely an expired domain redirect to spam
    if original_domain:
        orig_root = original_domain.lstrip("www.").split(".")[0]
        final_root = final_domain.split(".")[0]
        # If roots share no overlap, it's suspicious
        if len(orig_root) >= 4 and orig_root not in final_root and final_root not in orig_root:
            log.debug(f"  Skipping redirect: {original_domain} -> {final_domain}")
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
            # Clean URL
            if final_url.startswith("http://"):
                final_url = "https://" + final_url[7:]
            # Strip tracking params
            final_url = re.sub(r"\?.*$", "", final_url).rstrip("/")
            return final_url

    return ""


# ---------------------------------------------------------------------------
# Step 3: Email discovery
# ---------------------------------------------------------------------------

def find_emails_on_site(url: str, fetcher: PoliteFetcher) -> list[str]:
    """Scrape a website for email addresses."""
    if not url:
        return []

    emails = set()

    # Pages most likely to have contact info
    pages_to_check = [
        url,
        f"{url}/pages/contact",
        f"{url}/pages/contact-us",
        f"{url}/contact",
        f"{url}/pages/about",
    ]

    for page_url in pages_to_check:
        resp = fetcher.fetch(page_url)
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
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Enrich signals with URLs and emails")
    parser.add_argument("--limit", type=int, default=50, help="Max signals to process")
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
    log.info(f"Found {len(signals)} signals without company URL")

    if not signals:
        return

    if args.dry_run:
        for s in signals[:20]:
            log.info(f"  Would enrich: {s['author_name']}")
        if len(signals) > 20:
            log.info(f"  ... and {len(signals) - 20} more")
        return

    fetcher = PoliteFetcher(min_delay=1.0, max_delay=2.0)
    resolved = 0
    emails_found = 0

    try:
        for i, signal in enumerate(signals, 1):
            name = signal["author_name"]
            log.info(f"[{i}/{len(signals)}] Enriching: {name}")

            # Step 1: Try myshopify subdomain probing
            url = try_myshopify(name, fetcher)
            source = "myshopify"

            # Step 2: Try direct domain probing
            if not url:
                url = try_direct_domains(name, fetcher)
                source = "domain"

            if url:
                log.info(f"  URL found ({source}): {url}")
                update_company_url(signal["id"], url, DB_PATH)
                resolved += 1

                # Step 3: Try to find email
                emails = find_emails_on_site(url, fetcher)
                if emails:
                    domain = re.sub(r"^https?://", "", url).split("/")[0]
                    best = pick_best_email(emails, domain)
                    log.info(f"  Email found: {best}")
                    update_email(signal["id"], best, DB_PATH)
                    emails_found += 1
                else:
                    log.info(f"  No email found on site")
            else:
                log.info(f"  Could not find URL")

    except KeyboardInterrupt:
        log.info("\nInterrupted — progress saved.")
    finally:
        fetcher.close()

    log.info(f"Done. Resolved {resolved}/{len(signals)} URLs, found {emails_found} emails.")


def enrich_emails(args):
    """Find emails for signals that already have a company URL."""
    signals = get_signals_missing_email(limit=args.limit, db_path=DB_PATH)
    log.info(f"Found {len(signals)} signals with URL but no email")

    if not signals:
        return

    if args.dry_run:
        for s in signals[:20]:
            log.info(f"  Would check: {s['author_name']} -> {s['author_company_url']}")
        if len(signals) > 20:
            log.info(f"  ... and {len(signals) - 20} more")
        return

    fetcher = PoliteFetcher(min_delay=2.0, max_delay=4.0)
    found = 0

    try:
        for i, signal in enumerate(signals, 1):
            name = signal["author_name"]
            url = signal["author_company_url"]
            log.info(f"[{i}/{len(signals)}] Finding email: {name} ({url})")

            emails = find_emails_on_site(url, fetcher)
            if emails:
                domain = re.sub(r"^https?://", "", url).split("/")[0]
                best = pick_best_email(emails, domain)
                log.info(f"  Email: {best}")
                update_email(signal["id"], best, DB_PATH)
                found += 1
            else:
                log.info(f"  No email found")

    except KeyboardInterrupt:
        log.info("\nInterrupted — progress saved.")
    finally:
        fetcher.close()

    log.info(f"Done. Found emails for {found}/{len(signals)} signals.")


if __name__ == "__main__":
    main()
