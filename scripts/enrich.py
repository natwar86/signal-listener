#!/usr/bin/env python3
"""
Company-level enrichment: resolve store websites, verify them, find emails.

Operates on the companies table (one row per store, deduped across apps),
never on signal rows — collection syncs can't clobber this work.

Resolution ladder (provenance recorded in resolution_source):
  1. myshopify.com slug probing — Shopify redirects a shop's myshopify
     subdomain to its custom domain, so a hit is near-authoritative
  2. Exa search — high hit rate but can return lookalikes
  3. direct domain probing — weakest, pattern guessing

Every resolved URL gets a verification pass (resolution_confidence):
  verified   — store name matches the page AND the site runs Shopify
  likely     — name matches but no Shopify fingerprint (replatformed?)
  unverified — Shopify site but name doesn't match (possible wrong store)
  rejected   — neither matches; export drops these from the dashboard
  exhausted  — resolution failed MAX_RESOLVE_ATTEMPTS times; never retried
               automatically (only via --retry-failed)

Usage:
    python -m scripts.enrich                  # resolve+verify+email, 50 companies
    python -m scripts.enrich --limit 200
    python -m scripts.enrich --hot-warm-only  # only companies with hot/warm signals
    python -m scripts.enrich --verify-only    # re-verify already-resolved companies
    python -m scripts.enrich --emails-only    # only scrape emails for resolved companies
    python -m scripts.enrich --retry-failed   # retry companies that failed before
    python -m scripts.enrich --dry-run
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

from db import get_connection, init_db, update_company
from config import DB_PATH, EXA_API_KEY
from collectors.base import PoliteFetcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("signal-listener")

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# After this many failed resolutions a company is tagged 'exhausted' and
# excluded from future runs (override with --retry-failed).
MAX_RESOLVE_ATTEMPTS = 5

SKIP_EMAIL_DOMAINS = {
    "example.com", "sentry.io", "wixpress.com", "shopify.com",
    "cloudflare.com", "googleapis.com", "google.com", "facebook.com",
    "twitter.com", "schema.org", "w3.org", "jquery.com",
    "gravatar.com", "wordpress.org", "wordpress.com", "myshopify.com",
}
SKIP_EMAIL_PREFIXES = {"noreply", "no-reply", "mailer-daemon", "postmaster"}
SKIP_EMAILS = {
    "your@email.com", "email@example.com", "name@example.com",
    "info@example.com", "test@test.com", "admin@example.com",
}

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

# Words too generic to prove a name match on their own
NAME_STOPWORDS = {
    "the", "shop", "store", "inc", "llc", "ltd", "co", "corp", "company",
    "usa", "official", "online", "wholesale", "retail", "collection",
    "brand", "brands", "and", "of", "for",
}

_db_lock = threading.Lock()

# Most stores sit behind Shopify's shared edge, which rate-limits per client
# IP across ALL stores — pacing must be global across workers, not per-fetcher
# (each company gets a fresh fetcher, so per-fetcher delays never kick in).
FETCH_MIN_DELAY = 2.0
FETCH_MAX_DELAY = 4.0
FETCH_RETRIES = 2

_pace_lock = threading.Lock()
_last_fetch_time = [0.0]


def _pace():
    """Global politeness gate: at most one outbound request per ~FETCH_MIN_DELAY
    across all threads. Sleeping inside the lock is intentional — it serializes
    request starts."""
    import time
    import random
    with _pace_lock:
        wait = random.uniform(FETCH_MIN_DELAY, FETCH_MAX_DELAY) - (
            time.monotonic() - _last_fetch_time[0])
        if wait > 0:
            time.sleep(wait)
        _last_fetch_time[0] = time.monotonic()


class PacedFetcher(PoliteFetcher):
    def fetch(self, *args, **kwargs):
        _pace()
        return super().fetch(*args, **kwargs)

    def head(self, *args, **kwargs):
        _pace()
        return super().head(*args, **kwargs)


def _make_fetcher() -> PoliteFetcher:
    # Per-fetcher delay stays minimal; the global _pace() does the real pacing.
    return PacedFetcher(min_delay=0.1, max_delay=0.2)


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------

def get_companies_to_resolve(limit: int, hot_warm_only: bool = False,
                             retry_failed: bool = False, db_path=None):
    """Companies without a URL, hottest signals first.

    Never-attempted companies come before failed retries; a company that has
    failed MAX_RESOLVE_ATTEMPTS times is tagged 'exhausted' and skipped for
    good. --retry-failed lifts both the cap and the exhausted tag."""
    conn = get_connection(db_path or DB_PATH)
    retry_clause = "" if retry_failed else (
        f"AND COALESCE(c.resolve_attempts, 0) < {MAX_RESOLVE_ATTEMPTS} "
        "AND COALESCE(c.resolution_confidence, '') != 'exhausted'"
    )
    hw_clause = "AND pri <= 1" if hot_warm_only else ""
    rows = conn.execute(f"""
        SELECT c.id, c.name,
               COALESCE(c.resolve_attempts, 0) AS resolve_attempts,
               MIN(CASE s.urgency WHEN 'hot' THEN 0 WHEN 'warm' THEN 1 ELSE 2 END) AS pri,
               MAX(s.timestamp) AS latest
        FROM companies c
        JOIN signals s ON s.company_id = c.id
        WHERE (c.url IS NULL OR c.url = '')
          AND (c.resolution_confidence IS NULL OR c.resolution_confidence != 'rejected')
          {retry_clause}
        GROUP BY c.id
        HAVING 1 {hw_clause}
        ORDER BY pri, (c.enriched_at IS NULL) DESC, COALESCE(c.resolve_attempts, 0), latest DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_companies_to_verify(limit: int, db_path=None):
    """Resolved companies whose URL hasn't been verified yet."""
    conn = get_connection(db_path or DB_PATH)
    rows = conn.execute("""
        SELECT id, name, url FROM companies
        WHERE url IS NOT NULL AND url != ''
          AND (verified_at IS NULL OR resolution_confidence IS NULL)
          AND COALESCE(resolution_confidence, '') != 'rejected'
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_companies_missing_email(limit: int, db_path=None):
    """Verified/likely companies with a URL but no email."""
    conn = get_connection(db_path or DB_PATH)
    rows = conn.execute("""
        SELECT id, name, url FROM companies
        WHERE url IS NOT NULL AND url != ''
          AND (email IS NULL OR email = '')
          AND COALESCE(resolution_confidence, '') NOT IN ('rejected', 'unverified')
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_company(company_id: int, fields: dict):
    with _db_lock:
        update_company(company_id, fields, db_path=DB_PATH)


# ---------------------------------------------------------------------------
# Resolution ladder
# ---------------------------------------------------------------------------

def generate_myshopify_slugs(name: str) -> list[str]:
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
        return re.sub(r"[\s-]+", "-", s).strip("-")

    candidates = []
    for variant in [name, stripped]:
        slug = make_slug(variant)
        if slug and len(slug) >= 3:
            for c in (slug, slug.replace("-", "")):
                if c not in candidates:
                    candidates.append(c)
            if slug.startswith("the-"):
                for c in (slug[4:], slug[4:].replace("-", "")):
                    if c not in candidates:
                        candidates.append(c)

    return [c for c in candidates if 3 <= len(c) <= 60][:6]


def try_myshopify(name: str, fetcher: PoliteFetcher) -> str:
    """Probe <slug>.myshopify.com; Shopify redirects to the custom domain."""
    for slug in generate_myshopify_slugs(name):
        resp = fetcher.head(f"https://{slug}.myshopify.com")
        if resp is None:
            continue
        store_url = ""
        for hist in resp.history:
            loc = hist.headers.get("Location", "")
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


def try_exa_search(name: str) -> str:
    if not EXA_API_KEY or len(name.strip()) < 3:
        return ""
    import requests as _requests
    try:
        resp = _requests.post(
            "https://api.exa.ai/search",
            headers={"x-api-key": EXA_API_KEY, "Content-Type": "application/json"},
            json={"query": f'"{name}" official website', "type": "auto",
                  "numResults": 3, "category": "company"},
            timeout=15,
        )
        if resp.status_code != 200:
            log.warning(f"  Exa API error: {resp.status_code}")
            return ""
        name_words = set(re.sub(r"[^a-z0-9\s]", "", name.lower()).split())
        for r in resp.json().get("results", []):
            url = r.get("url", "")
            if not url:
                continue
            domain = urlparse(url).netloc.lower().lstrip("www.")
            if any(s in domain for s in SPAM_DOMAINS):
                continue
            if any(m in domain for m in SKIP_MARKETPLACE_DOMAINS):
                continue
            domain_root = domain.split(".")[0]
            if any(w in domain_root for w in name_words if len(w) >= 3):
                return f"https://{domain}"
    except Exception as e:
        log.warning(f"  Exa search failed: {e}")
    return ""


def generate_domain_candidates(name: str) -> list[str]:
    if not name:
        return []
    norm = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower().strip()
    cleaned = re.sub(
        r"\b(inc|llc|ltd|co|corp|company|store|shop|usa|us|uk|au|ca|"
        r"official|online|the|wholesale|retail)\b\.?\s*", "", norm,
    ).strip()

    def slugify(s):
        return re.sub(r"[^a-z0-9]", "", s)

    slug = slugify(cleaned)
    slug_full = slugify(norm)
    if not slug or len(slug) < 3:
        return []

    domains = []
    for s in dict.fromkeys([slug, slug_full]):
        if len(s) >= 3:
            domains += [f"{s}.com", f"shop{s}.com", f"{s}.co"]
    if "&" in name:
        and_version = slugify(norm.replace("&", "and"))
        if and_version and and_version != slug:
            domains.append(f"{and_version}.com")
    return [d for d in dict.fromkeys(domains) if len(d.split(".")[0]) <= 60][:8]


def try_direct_domains(name: str, fetcher: PoliteFetcher) -> str:
    for domain in generate_domain_candidates(name):
        resp = fetcher.head(f"https://{domain}")
        if resp is None:
            continue
        final_url = resp.url.rstrip("/")
        final_domain = urlparse(final_url).netloc.lower().lstrip("www.")
        if resp.status_code >= 400:
            continue
        if any(s in final_domain for s in SPAM_DOMAINS):
            continue
        # Redirect landing far from the probed domain is a parked/spam tell
        probe_root = domain.split(".")[0]
        final_root = final_domain.split(".")[0]
        if len(probe_root) >= 4 and probe_root not in final_root and final_root not in probe_root:
            continue
        if final_url.startswith("http://"):
            final_url = "https://" + final_url[7:]
        return re.sub(r"\?.*$", "", final_url).rstrip("/")
    return ""


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

SHOPIFY_FINGERPRINTS = ("cdn.shopify.com", "myshopify.com", "Shopify.theme",
                        "shopify-features", "cdn/shop/")


def significant_words(name: str) -> list[str]:
    norm = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    words = re.sub(r"[^a-z0-9\s]", " ", norm).split()
    return [w for w in words if len(w) >= 3 and w not in NAME_STOPWORDS]


def verify_company_page(name: str, url: str, html: str) -> str:
    """Return a resolution_confidence for a fetched homepage."""
    is_shopify = any(fp in html for fp in SHOPIFY_FINGERPRINTS)

    head = html[:20000].lower()
    title_match = re.search(r"<title[^>]*>(.*?)</title>", head, re.DOTALL)
    title = title_match.group(1) if title_match else ""
    site_name = ""
    m = re.search(r'property=["\']og:site_name["\']\s+content=["\']([^"\']+)', head)
    if m:
        site_name = m.group(1)
    haystack = f"{title} {site_name} {urlparse(url).netloc.lower()}"

    words = significant_words(name)
    squashed = re.sub(r"[^a-z0-9]", "", haystack)
    name_match = bool(words) and (
        any(w in haystack for w in words)
        or re.sub(r"[^a-z0-9]", "", "".join(words)) in squashed
    )

    if name_match and is_shopify:
        return "verified"
    if name_match:
        return "likely"
    if is_shopify:
        return "unverified"
    return "rejected"


# ---------------------------------------------------------------------------
# Email discovery
# ---------------------------------------------------------------------------

def extract_emails(html: str) -> set[str]:
    emails = set()
    for email in EMAIL_REGEX.findall(html):
        email = email.lower().strip()
        domain = email.split("@")[1] if "@" in email else ""
        prefix = email.split("@")[0] if "@" in email else ""
        if domain in SKIP_EMAIL_DOMAINS or prefix in SKIP_EMAIL_PREFIXES:
            continue
        if email.endswith((".png", ".jpg", ".svg", ".gif", ".webp")):
            continue
        if any(x in email for x in ["{", "}", "//", "/*", "webpack", "woff"]):
            continue
        if email in SKIP_EMAILS:
            continue
        emails.add(email)
    return emails


def pick_best_email(emails: list[str], domain: str) -> str:
    if not emails:
        return ""
    domain_root = ".".join(domain.split(".")[-2:]).lower()
    domain_emails = [e for e in emails if domain_root in e]
    for prefix in ["hello", "hi", "info", "contact", "support", "help",
                   "team", "sales", "admin"]:
        for e in domain_emails:
            if e.startswith(f"{prefix}@"):
                return e
    if domain_emails:
        return domain_emails[0]
    return sorted(emails)[0]


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------

def process_company(company: dict, index: int, total: int) -> dict:
    """Resolve + verify + email one company. One homepage fetch serves both
    verification and email scraping."""
    name = company["name"]
    result = {"id": company["id"], "name": name, "url": "",
              "confidence": "", "email": "", "source": ""}
    fetcher = _make_fetcher()
    try:
        url, source = "", ""
        url = try_myshopify(name, fetcher)
        if url:
            source = "myshopify"
        if not url:
            url = try_exa_search(name)
            if url:
                source = "exa"
        if not url:
            url = try_direct_domains(name, fetcher)
            if url:
                source = "domain"

        if not url:
            attempts = (company.get("resolve_attempts") or 0) + 1
            fields = {
                "enriched_at": _now(),
                "resolve_attempts": attempts,
                "notes": f"resolution failed (attempt {attempts})",
            }
            if attempts >= MAX_RESOLVE_ATTEMPTS:
                fields["resolution_confidence"] = "exhausted"
            log.info(f"[{index}/{total}] {name} -> not found "
                     f"(attempt {attempts}/{MAX_RESOLVE_ATTEMPTS})")
            save_company(company["id"], fields)
            return result

        # Verify + scrape with a single homepage fetch
        confidence = "unverified"
        email = ""
        resp = fetcher.fetch(url, max_retries=FETCH_RETRIES)
        if resp is not None:
            confidence = verify_company_page(name, url, resp.text)
            emails = extract_emails(resp.text)
            if not emails and confidence != "rejected":
                contact_resp = fetcher.fetch(f"{url}/pages/contact", max_retries=FETCH_RETRIES)
                if contact_resp is not None:
                    emails = extract_emails(contact_resp.text)
            if emails:
                email = pick_best_email(sorted(emails), urlparse(url).netloc)
        else:
            confidence = "likely" if source == "myshopify" else "unverified"

        domain = urlparse(url).netloc.lower()
        fields = {
            "url": url,
            "domain": domain[4:] if domain.startswith("www.") else domain,
            "resolution_source": source,
            "resolution_confidence": confidence,
            "verified_at": _now() if resp is not None else None,
            "enriched_at": _now(),
        }
        if email:
            fields["email"] = email
        save_company(company["id"], fields)

        log.info(f"[{index}/{total}] {name} -> {url} ({source}, {confidence}"
                 + (f", {email}" if email else "") + ")")
        result.update(url=url, confidence=confidence, email=email, source=source)
    except Exception as e:
        log.warning(f"[{index}/{total}] Error enriching {name}: {e}")
    finally:
        fetcher.close()
    return result


def verify_one(company: dict, index: int, total: int) -> str:
    """Verification-only pass for an already-resolved company."""
    name, url = company["name"], company["url"]
    fetcher = _make_fetcher()
    try:
        resp = fetcher.fetch(url, max_retries=FETCH_RETRIES)
        if resp is None:
            # Site unreachable — record the attempt so the verify queue drains;
            # can't confirm the URL, so it stays visible but low-confidence.
            save_company(company["id"], {
                "resolution_confidence": "unverified", "verified_at": _now(),
                "notes": "verify fetch failed",
            })
            log.info(f"[{index}/{total}] {name}: fetch failed -> unverified")
            return "unverified"
        confidence = verify_company_page(name, url, resp.text)
        save_company(company["id"], {
            "resolution_confidence": confidence, "verified_at": _now(),
        })
        log.info(f"[{index}/{total}] {name} ({url}) -> {confidence}")
        return confidence
    except Exception as e:
        log.warning(f"[{index}/{total}] Error verifying {name}: {e}")
        return ""
    finally:
        fetcher.close()


def email_one(company: dict, index: int, total: int) -> str:
    name, url = company["name"], company["url"]
    fetcher = _make_fetcher()
    try:
        emails = set()
        for page in (url, f"{url}/pages/contact"):
            resp = fetcher.fetch(page, max_retries=FETCH_RETRIES)
            if resp is not None:
                emails |= extract_emails(resp.text)
            if emails:
                break
        if emails:
            best = pick_best_email(sorted(emails), urlparse(url).netloc)
            save_company(company["id"], {"email": best})
            log.info(f"[{index}/{total}] {name}: {best}")
            return best
        log.info(f"[{index}/{total}] {name}: no email")
        return ""
    except Exception as e:
        log.warning(f"[{index}/{total}] Error for {name}: {e}")
        return ""
    finally:
        fetcher.close()


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _run_pool(items, worker, workers):
    done = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(worker, item, i, len(items)): item
                   for i, item in enumerate(items, 1)}
        try:
            for future in as_completed(futures):
                done.append(future.result())
        except KeyboardInterrupt:
            log.info("\nInterrupted — progress saved.")
            pool.shutdown(wait=False, cancel_futures=True)
    return done


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Company-level enrichment")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--hot-warm-only", action="store_true",
                        help="Only companies with hot/warm signals")
    parser.add_argument("--verify-only", action="store_true",
                        help="Re-verify already-resolved companies")
    parser.add_argument("--emails-only", action="store_true",
                        help="Only scrape emails for resolved companies")
    parser.add_argument("--retry-failed", action="store_true",
                        help="Ignore the attempt cap and the 'exhausted' tag "
                             "(failures under the cap retry automatically)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    init_db(DB_PATH)

    if args.verify_only:
        companies = get_companies_to_verify(args.limit)
        log.info(f"{len(companies)} companies to verify (workers={args.workers})")
        if args.dry_run:
            for c in companies[:20]:
                log.info(f"  Would verify: {c['name']} -> {c['url']}")
            return
        results = _run_pool(companies, verify_one, args.workers)
        from collections import Counter
        log.info(f"Done. {Counter(r for r in results if r)}")
        return

    if args.emails_only:
        companies = get_companies_missing_email(args.limit)
        log.info(f"{len(companies)} companies missing email (workers={args.workers})")
        if args.dry_run:
            for c in companies[:20]:
                log.info(f"  Would check: {c['name']} -> {c['url']}")
            return
        results = _run_pool(companies, email_one, args.workers)
        log.info(f"Done. Found {sum(1 for r in results if r)} emails.")
        return

    companies = get_companies_to_resolve(
        args.limit, hot_warm_only=args.hot_warm_only, retry_failed=args.retry_failed)
    log.info(f"{len(companies)} companies to resolve (workers={args.workers})")
    if args.dry_run:
        for c in companies[:20]:
            log.info(f"  Would resolve: {c['name']} (pri={c['pri']})")
        if len(companies) > 20:
            log.info(f"  ... and {len(companies) - 20} more")
        return
    results = _run_pool(companies, process_company, args.workers)
    resolved = [r for r in results if r["url"]]
    log.info(f"Done. Resolved {len(resolved)}/{len(companies)}; "
             f"{sum(1 for r in resolved if r['confidence'] == 'verified')} verified, "
             f"{sum(1 for r in resolved if r['email'])} with email.")


if __name__ == "__main__":
    main()
