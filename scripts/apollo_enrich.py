#!/usr/bin/env python3
"""
Enrich signals with contact info from Apollo.io.

Given company domains, finds decision-makers (founder/owner/CEO) and their
verified emails using Apollo's API.

Flow:
  1. People Search (free, no credits) — find people at domain with target titles
  2. People Enrichment (1 credit each) — get verified email for best match

Usage:
    python -m scripts.apollo_enrich                 # Enrich up to 50 signals
    python -m scripts.apollo_enrich --limit 200     # Enrich more
    python -m scripts.apollo_enrich --dry-run       # Preview without API calls
    python -m scripts.apollo_enrich --workers 5     # Parallel workers (default: 3)
"""

import re
import sys
import json
import logging
import argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import get_connection, init_db
from config import DB_PATH, APOLLO_API_KEY

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("signal-listener")

_db_lock = threading.Lock()

# Titles we want to reach for cold outreach (decision-makers)
TARGET_TITLES = [
    "founder", "co-founder", "cofounder",
    "owner", "co-owner",
    "ceo", "chief executive",
    "president",
    "director of operations", "head of operations", "vp operations",
    "director of fulfillment", "head of fulfillment",
    "director of supply chain", "head of supply chain",
    "director of logistics", "head of logistics",
    "general manager", "managing director",
]

# Rate limit: 600 calls/hour = 10/min. Stay well under.
_rate_lock = threading.Lock()
_last_call_time = 0.0
MIN_CALL_INTERVAL = 0.3  # ~200 calls/min max


def _rate_limit():
    """Simple rate limiter for Apollo API."""
    global _last_call_time
    with _rate_lock:
        now = time.monotonic()
        elapsed = now - _last_call_time
        if elapsed < MIN_CALL_INTERVAL:
            time.sleep(MIN_CALL_INTERVAL - elapsed)
        _last_call_time = time.monotonic()


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_signals_for_apollo(limit: int = 50):
    """Get signals with a company URL but no Apollo contact info."""
    conn = get_connection(DB_PATH)
    rows = conn.execute("""
        SELECT id, author_name, author_company_url, raw_json
        FROM signals
        WHERE author_company_url IS NOT NULL
          AND author_company_url != ''
          AND (raw_json NOT LIKE '%"apollo_contact"%'
               OR raw_json LIKE '%"apollo_contact": null%')
        ORDER BY
            CASE WHEN urgency = 'hot' THEN 0
                 WHEN urgency = 'warm' THEN 1
                 ELSE 2 END,
            timestamp DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_apollo_contact(signal_id: str, contact: dict):
    """Save Apollo contact info to signal's raw_json metadata."""
    with _db_lock:
        conn = get_connection(DB_PATH)
        row = conn.execute(
            "SELECT raw_json FROM signals WHERE id = ?", (signal_id,)
        ).fetchone()
        if row:
            d = json.loads(row["raw_json"])
            if "metadata" not in d:
                d["metadata"] = {}
            d["metadata"]["apollo_contact"] = contact
            # Also set the email field if we got one
            if contact.get("email"):
                d["metadata"]["email"] = contact["email"]
            conn.execute(
                "UPDATE signals SET raw_json = ? WHERE id = ?",
                (json.dumps(d), signal_id),
            )
        conn.commit()
        conn.close()


# ---------------------------------------------------------------------------
# Apollo API
# ---------------------------------------------------------------------------

def extract_domain(url: str) -> str:
    """Extract clean domain from a URL."""
    domain = re.sub(r"^https?://", "", url).split("/")[0]
    domain = domain.lstrip("www.")
    return domain


def apollo_search_people(domain: str) -> list[dict]:
    """
    Search for people at a company by domain. Free, no credits consumed.
    Returns list of person dicts with id, name, title.
    """
    _rate_limit()

    try:
        resp = requests.post(
            "https://api.apollo.io/api/v1/mixed_people/api_search",
            headers={
                "x-api-key": APOLLO_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "organization_domains": [domain],
                "person_titles": TARGET_TITLES,
                "per_page": 10,
                "page": 1,
            },
            timeout=15,
        )

        if resp.status_code == 429:
            log.warning("  Apollo rate limited, sleeping 60s")
            time.sleep(60)
            return apollo_search_people(domain)

        if resp.status_code != 200:
            log.warning(f"  Apollo search error: {resp.status_code}")
            return []

        data = resp.json()
        people = data.get("people", [])
        return people

    except Exception as e:
        log.warning(f"  Apollo search failed: {e}")
        return []


def apollo_enrich_person(person_id: str = None, name: str = None,
                         domain: str = None) -> dict | None:
    """
    Enrich a person to get their email. Costs 1 credit.
    Can use person_id (from search) or name+domain.
    """
    _rate_limit()

    payload = {}
    if person_id:
        payload["id"] = person_id
    elif name and domain:
        parts = name.split(None, 1)
        if len(parts) == 2:
            payload["first_name"] = parts[0]
            payload["last_name"] = parts[1]
        else:
            payload["name"] = name
        payload["domain"] = domain
    else:
        return None

    payload["reveal_personal_emails"] = False

    try:
        resp = requests.post(
            "https://api.apollo.io/api/v1/people/match",
            headers={
                "x-api-key": APOLLO_API_KEY,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15,
        )

        if resp.status_code == 429:
            log.warning("  Apollo rate limited, sleeping 60s")
            time.sleep(60)
            return apollo_enrich_person(person_id, name, domain)

        if resp.status_code != 200:
            log.warning(f"  Apollo enrich error: {resp.status_code}")
            return None

        data = resp.json()
        person = data.get("person")
        return person

    except Exception as e:
        log.warning(f"  Apollo enrich failed: {e}")
        return None


def pick_best_person(people: list[dict]) -> dict | None:
    """Pick the best decision-maker from search results."""
    if not people:
        return None

    # Score by title relevance
    def title_score(person):
        title = (person.get("title") or "").lower()
        if any(t in title for t in ["founder", "co-founder", "cofounder", "owner"]):
            return 0
        if any(t in title for t in ["ceo", "chief executive", "president"]):
            return 1
        if any(t in title for t in ["director", "head of", "vp"]):
            return 2
        if "manager" in title:
            return 3
        return 4

    scored = sorted(people, key=title_score)
    return scored[0]


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

def enrich_one(signal: dict, index: int, total: int) -> dict:
    """Enrich a single signal with Apollo contact data."""
    name = signal["author_name"]
    url = signal["author_company_url"]
    domain = extract_domain(url)
    result = {"id": signal["id"], "name": name, "contact": None}

    try:
        # Step 1: Search for people at this domain (free)
        people = apollo_search_people(domain)

        if people:
            best = pick_best_person(people)
            if best:
                person_name = best.get("name", "")
                person_title = best.get("title", "")

                # Step 2: Enrich to get email (1 credit)
                enriched = apollo_enrich_person(
                    person_id=best.get("id"),
                    name=person_name,
                    domain=domain,
                )

                if enriched and enriched.get("email"):
                    contact = {
                        "name": enriched.get("name", person_name),
                        "title": enriched.get("title", person_title),
                        "email": enriched["email"],
                        "email_status": enriched.get("email_status", ""),
                        "linkedin_url": enriched.get("linkedin_url", ""),
                    }
                    log.info(f"[{index}/{total}] {name} ({domain}) -> "
                             f"{contact['name']} ({contact['title']}) "
                             f"<{contact['email']}>")
                    save_apollo_contact(signal["id"], contact)
                    result["contact"] = contact
                    return result
                elif enriched:
                    # Got person but no email
                    contact = {
                        "name": enriched.get("name", person_name),
                        "title": enriched.get("title", person_title),
                        "email": "",
                        "linkedin_url": enriched.get("linkedin_url", ""),
                    }
                    log.info(f"[{index}/{total}] {name} ({domain}) -> "
                             f"{contact['name']} ({contact['title']}) "
                             f"[no email]")
                    save_apollo_contact(signal["id"], contact)
                    result["contact"] = contact
                    return result

        # No people found with target titles — try enrichment with domain only
        enriched = apollo_enrich_person(domain=domain, name=name)
        if enriched and enriched.get("email"):
            contact = {
                "name": enriched.get("name", ""),
                "title": enriched.get("title", ""),
                "email": enriched["email"],
                "email_status": enriched.get("email_status", ""),
                "linkedin_url": enriched.get("linkedin_url", ""),
            }
            log.info(f"[{index}/{total}] {name} ({domain}) -> "
                     f"{contact['name']} ({contact['title']}) "
                     f"<{contact['email']}> [domain match]")
            save_apollo_contact(signal["id"], contact)
            result["contact"] = contact
        else:
            log.info(f"[{index}/{total}] {name} ({domain}) -> no contact found")
            # Save empty so we don't retry
            save_apollo_contact(signal["id"], {"name": "", "email": "", "title": ""})

    except Exception as e:
        log.warning(f"[{index}/{total}] Error for {name}: {e}")

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Enrich signals with Apollo.io contacts")
    parser.add_argument("--limit", type=int, default=50, help="Max signals to process")
    parser.add_argument("--workers", type=int, default=3, help="Parallel workers (default: 3)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without API calls")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if not APOLLO_API_KEY:
        log.error("APOLLO_API_KEY not set. Add it to .env file.")
        sys.exit(1)

    init_db(DB_PATH)

    signals = get_signals_for_apollo(limit=args.limit)
    log.info(f"Found {len(signals)} signals to enrich with Apollo (workers={args.workers})")

    if not signals:
        return

    if args.dry_run:
        for s in signals[:20]:
            domain = extract_domain(s["author_company_url"])
            log.info(f"  Would enrich: {s['author_name']} ({domain})")
        if len(signals) > 20:
            log.info(f"  ... and {len(signals) - 20} more")
        return

    found = 0
    with_email = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(enrich_one, sig, i, len(signals)): sig
            for i, sig in enumerate(signals, 1)
        }

        try:
            for future in as_completed(futures):
                result = future.result()
                if result["contact"]:
                    found += 1
                    if result["contact"].get("email"):
                        with_email += 1
        except KeyboardInterrupt:
            log.info("\nInterrupted — progress saved.")
            pool.shutdown(wait=False, cancel_futures=True)

    log.info(f"Done. Found {found} contacts ({with_email} with email) "
             f"out of {len(signals)} signals.")


if __name__ == "__main__":
    main()
