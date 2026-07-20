"""
G2 + Capterra reviews collector via Apify zen-studio/software-review-scraper.

Pay-per-event pricing: $0.05/start + ~$0.005/review (free tier) — roughly
10x Trustpilot's rate (G2's bot protection is priced in), which drives two
design choices:
  - one actor call per brand (the actor takes a single product query),
    capped per brand rather than pulled exhaustively
  - the pipeline runs this MONTHLY, not per-cron: the actor has no date
    filter and maxResults floors at 100/brand, so every run re-pays for
    the newest slice whether or not it's new to us

Like Trustpilot, reviewers are anonymized people ("Verified User in
Manufacturing") — author.company stays empty, no company rows are created.
The G2/Capterra-specific value is structured buyer metadata (segment,
industry) plus pros/cons text for the classifier and intelligence feed.
"""

import logging
from typing import Optional

from processor.schema import Signal, Author, Content
from db import insert_signal
from collectors.base import apify_run_info
from config import (
    APIFY_API_TOKEN,
    APIFY_SOFTWARE_REVIEWS_ACTOR,
    APIFY_SR_COST_PER_RUN,
    APIFY_SR_COST_PER_REVIEW,
    DB_PATH,
)

log = logging.getLogger("signal-listener")


def estimate_cost(num_brands: int, max_reviews_per_brand: int) -> dict:
    reviews_total = num_brands * max_reviews_per_brand
    cost = num_brands * APIFY_SR_COST_PER_RUN + reviews_total * APIFY_SR_COST_PER_REVIEW
    return {
        "brands": num_brands,
        "max_reviews_total": reviews_total,
        "estimated_usd": round(cost, 4),
    }


def _brand_matches(product_name: str, brand: str) -> bool:
    """The actor resolves a free-text query to a product; guard against a
    lookalike product being returned for a brand query."""
    return brand.lower().replace(" ", "") in (product_name or "").lower().replace(" ", "")


def review_to_signal(item: dict, brand: str) -> Optional[Signal]:
    """Convert one G2/Capterra item into a Signal.
    G2 reviews often have null text with the content in pros/cons."""
    review_id = item.get("reviewId") or ""
    platform = item.get("platform") or ""
    if not review_id or platform not in ("g2", "capterra"):
        return None

    body = item.get("text") or ""
    if not body:
        parts = []
        if item.get("pros"):
            parts.append(f"Pros: {item['pros']}")
        if item.get("cons"):
            parts.append(f"Cons: {item['cons']}")
        body = "\n".join(parts)
    if not body:
        return None

    rating = item.get("rating")
    try:
        rating = float(rating) if rating is not None else None
    except (TypeError, ValueError):
        rating = None

    reviewer = item.get("reviewer") or {}
    date = item.get("date") or ""

    return Signal(
        id=f"{platform}_{review_id}",
        source=platform,
        source_url=item.get("reviewUrl") or "",
        timestamp=f"{date}T00:00:00+00:00" if date else "",
        author=Author(
            name=reviewer.get("name"),
            # company stays empty: G2/Capterra reviewers are anonymized;
            # filling this would create junk rows via migrate_companies
        ),
        content=Content(
            title=item.get("title"),
            body=body,
            rating=rating,
        ),
        metadata={
            "company_brand": brand,
            "product_name": item.get("productName", ""),
            "segment": reviewer.get("jobTitle", ""),
            "industry": reviewer.get("industry", ""),
            "company_size": reviewer.get("companySize", ""),
            "verified": reviewer.get("verified", False),
            "incentivized": item.get("incentivized", False),
            "pros": item.get("pros") or "",
            "cons": item.get("cons") or "",
            "recommendation_score": item.get("recommendationScore"),
        },
    )


def collect_software_reviews(
    brands: list[str],
    max_reviews_per_brand: int = 400,
    dry_run: bool = True,
    max_cost_usd: float = 15.00,
    db_path=DB_PATH,
) -> list[Signal]:
    """
    Scrape G2 + Capterra reviews for a list of brand names via Apify.
    One actor call per brand; newest reviews first, split across platforms
    proportionally by the actor.
    """
    if not brands:
        log.warning("No software-review brands configured.")
        return []

    estimate = estimate_cost(len(brands), max_reviews_per_brand)
    log.info(
        f"G2/Capterra cost estimate: ${estimate['estimated_usd']:.2f} "
        f"({len(brands)} brands × up to {max_reviews_per_brand} reviews)"
    )
    if estimate["estimated_usd"] > max_cost_usd:
        log.error(
            f"Estimated cost ${estimate['estimated_usd']:.2f} exceeds "
            f"max_cost_usd ${max_cost_usd:.2f}. Aborting."
        )
        return []

    if dry_run:
        log.info("DRY RUN — not calling Apify.")
        for b in brands:
            log.info(f"  would scrape: {b}")
        return []

    if not APIFY_API_TOKEN:
        log.error("APIFY_API_TOKEN not set. Add it to .env.")
        return []

    from apify_client import ApifyClient

    client = ApifyClient(APIFY_API_TOKEN)
    new_signals: list[Signal] = []

    for brand in brands:
        log.info(f"[{brand}] Calling {APIFY_SOFTWARE_REVIEWS_ACTOR}...")
        try:
            run = client.actor(APIFY_SOFTWARE_REVIEWS_ACTOR).call(run_input={
                "query": brand,
                "platforms": ["g2", "capterra"],
                "maxResults": max_reviews_per_brand,
                "sort": "most_recent",
            })
        except Exception as e:
            log.error(f"[{brand}] Actor call failed: {e}")
            continue

        status, dataset_id = apify_run_info(run)
        if "SUCCEEDED" not in status or not dataset_id:
            log.error(f"[{brand}] Run did not succeed: status={status!r}")
            continue

        total = inserted = mismatched = 0
        for item in client.dataset(dataset_id).iterate_items():
            total += 1
            if not _brand_matches(item.get("productName", ""), brand):
                mismatched += 1
                continue
            signal = review_to_signal(item, brand)
            if signal and insert_signal(signal, db_path):
                new_signals.append(signal)
                inserted += 1

        log.info(f"[{brand}] {total} items, {inserted} new signals inserted"
                 + (f", {mismatched} product mismatches skipped" if mismatched else ""))

    return new_signals
