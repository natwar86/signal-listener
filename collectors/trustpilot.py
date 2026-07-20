"""
Trustpilot reviews collector via Apify automation-lab/trustpilot.

Pay-per-event pricing: $0.005/run + ~$0.0006/review (free tier).
A full backfill of all target brands (~a few thousand reviews) is under $3;
weekly deltas are pennies.

Trustpilot reviewers are individuals — author names are people, not store
names — so these signals rarely resolve to companies. Their value is
switching-intent text for classification and the intelligence feed, not
enrichable leads. author.company is deliberately left empty so
migrate_companies doesn't create junk company rows from person names.

Safety (mirrors google_maps.py):
  - Defaults to dry-run (no API call) — pass dry_run=False to actually scrape.
  - Hard cost ceiling — refuses to run if estimate exceeds max_cost_usd.
  - Delta scrapes: once a brand has signals in the DB, cron runs fetch only
    the newest reviews (sort=recency) and stop on dedupe.
"""

import logging
from typing import Optional

from processor.schema import Signal, Author, Content
from db import insert_signal, get_connection
from collectors.base import apify_run_info
from config import (
    APIFY_API_TOKEN,
    APIFY_TRUSTPILOT_ACTOR,
    APIFY_TP_COST_PER_RUN,
    APIFY_TP_COST_PER_REVIEW,
    DB_PATH,
)

log = logging.getLogger("signal-listener")


def estimate_cost(num_companies: int, max_reviews_per_company: int) -> dict:
    """Worst-case cost estimate for an Apify run."""
    reviews_total = num_companies * max_reviews_per_company
    cost = APIFY_TP_COST_PER_RUN + reviews_total * APIFY_TP_COST_PER_REVIEW
    return {
        "companies": num_companies,
        "max_reviews_total": reviews_total,
        "estimated_usd": round(cost, 4),
    }


def has_existing_signals(db_path=DB_PATH) -> bool:
    conn = get_connection(db_path)
    count = conn.execute(
        "SELECT COUNT(*) FROM signals WHERE source='trustpilot'"
    ).fetchone()[0]
    conn.close()
    return count > 0


def review_to_signal(item: dict) -> Optional[Signal]:
    """Convert one Apify Trustpilot item into a Signal.
    Returns None for items with no review text."""
    review_id = item.get("reviewId") or ""
    text = item.get("text") or ""
    if not review_id or not text:
        return None

    rating = item.get("rating")
    try:
        rating = float(rating) if rating is not None else None
    except (TypeError, ValueError):
        rating = None

    return Signal(
        id=f"trustpilot_{review_id}",
        source="trustpilot",
        source_url=item.get("reviewUrl") or "",
        timestamp=item.get("publishedDate") or "",
        author=Author(
            name=item.get("authorName"),
            # company stays empty: Trustpilot authors are people, and filling
            # this would create junk rows via migrate_companies
        ),
        content=Content(
            title=item.get("title"),
            body=text,
            rating=rating,
        ),
        metadata={
            "company_brand": item.get("companyName", ""),
            "company_domain": item.get("companyDomain", ""),
            "company_total_reviews": item.get("companyTotalReviews"),
            "company_trust_score": item.get("companyTrustScore"),
            # country code doubles as the classifier prompt's {location}
            "location": item.get("country", ""),
            "experience_date": item.get("experienceDate", ""),
            "is_verified": item.get("isVerified", False),
            "author_review_count": item.get("authorReviewCount"),
            "reply_message": item.get("replyMessage", ""),
            "reply_date": item.get("replyPublishedDate", ""),
            "language": item.get("language", ""),
        },
    )


def collect_trustpilot_reviews(
    companies: list[dict],
    max_reviews_per_company: int = 200,
    date_preset: str = "",
    dry_run: bool = True,
    max_cost_usd: float = 5.00,
    db_path=DB_PATH,
) -> list[Signal]:
    """
    Scrape Trustpilot reviews for a set of brands via Apify.

    companies: list of {"name": str, "url": str} dicts, where url is the full
        Trustpilot review-page URL (business-unit slugs vary — some brands
        are keyed with a www. prefix, so always store the exact URL)
    max_reviews_per_company: per-brand cap (0 = unlimited, for backfill)
    date_preset: one of '', 'last30days', 'last3months', 'last6months',
        'last12months' — the actor-side recency filter for delta runs
    dry_run: if True, prints estimate and returns [] without calling Apify
    max_cost_usd: refuses to run if estimate exceeds this ceiling
    """
    if not companies:
        log.warning("No Trustpilot companies configured.")
        return []

    # 0 (unlimited) is only used for backfills; estimate against a sane bound
    est_reviews = max_reviews_per_company or 3000
    estimate = estimate_cost(len(companies), est_reviews)
    log.info(
        f"Trustpilot cost estimate: ${estimate['estimated_usd']:.4f} "
        f"({len(companies)} brands × up to {est_reviews} reviews)"
    )

    if estimate["estimated_usd"] > max_cost_usd:
        log.error(
            f"Estimated cost ${estimate['estimated_usd']:.4f} exceeds "
            f"max_cost_usd ${max_cost_usd:.2f}. Aborting."
        )
        return []

    if dry_run:
        log.info("DRY RUN — not calling Apify. Pass --live to actually scrape.")
        for c in companies:
            log.info(f"  would scrape: {c['name']} ({c['url']})")
        return []

    if not APIFY_API_TOKEN:
        log.error("APIFY_API_TOKEN not set. Add it to .env.")
        return []

    from apify_client import ApifyClient

    client = ApifyClient(APIFY_API_TOKEN)

    run_input = {
        "companyUrls": [c["url"] for c in companies],
        "maxReviewsPerCompany": max_reviews_per_company,
        "sort": "recency",
        "includeCompanyInfo": True,
        "maxTotalChargeUsd": max_cost_usd,
    }
    if date_preset:
        run_input["date"] = date_preset
        log.info(f"Delta scrape: {date_preset}")

    log.info(f"Calling actor {APIFY_TRUSTPILOT_ACTOR} for {len(companies)} brands...")
    run = client.actor(APIFY_TRUSTPILOT_ACTOR).call(run_input=run_input)

    status, dataset_id = apify_run_info(run)
    if "SUCCEEDED" not in status or not dataset_id:
        log.error(f"Actor run failed or did not succeed: status={status!r}")
        return []

    new_signals: list[Signal] = []
    total_seen = 0
    skipped_no_text = 0
    for item in client.dataset(dataset_id).iterate_items():
        total_seen += 1
        signal = review_to_signal(item)
        if signal is None:
            skipped_no_text += 1
            continue
        if insert_signal(signal, db_path):
            new_signals.append(signal)

    log.info(
        f"Apify returned {total_seen} items "
        f"(skipped {skipped_no_text} without text). "
        f"{len(new_signals)} new signals inserted."
    )
    return new_signals
