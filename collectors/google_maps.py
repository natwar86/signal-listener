"""
Google Maps reviews collector via Apify compass/google-maps-reviews-scraper.

Pay-per-event pricing: $0.007/run + $0.004/place + $0.0005/review.
At ~30-50 places × 200 reviews each, a full backfill is roughly $5.

Safety:
  - Defaults to dry-run (no API call) — pass dry_run=False to actually scrape.
  - Hard cost ceiling — refuses to run if estimate exceeds max_cost_usd.
  - Delta scrapes via reviews_start_date so weekly cron only fetches new reviews.
"""

import logging
from typing import Optional

from processor.schema import Signal, Author, Content
from db import insert_signal, get_connection
from config import (
    APIFY_API_TOKEN,
    APIFY_REVIEWS_ACTOR,
    APIFY_COST_PER_RUN,
    APIFY_COST_PER_PLACE,
    APIFY_COST_PER_REVIEW,
    DB_PATH,
)

log = logging.getLogger("signal-listener")


def estimate_cost(num_places: int, max_reviews_per_place: int) -> dict:
    """Worst-case cost estimate for an Apify run."""
    reviews_total = num_places * max_reviews_per_place
    cost = (
        APIFY_COST_PER_RUN
        + num_places * APIFY_COST_PER_PLACE
        + reviews_total * APIFY_COST_PER_REVIEW
    )
    return {
        "places": num_places,
        "max_reviews_per_place": max_reviews_per_place,
        "max_reviews_total": reviews_total,
        "estimated_usd": round(cost, 4),
        "breakdown": {
            "run": APIFY_COST_PER_RUN,
            "places": round(num_places * APIFY_COST_PER_PLACE, 4),
            "reviews": round(reviews_total * APIFY_COST_PER_REVIEW, 4),
        },
    }


def get_latest_review_date(db_path=DB_PATH) -> Optional[str]:
    """Most recent google_maps signal timestamp — used as delta cutoff."""
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT MAX(timestamp) FROM signals "
        "WHERE source='google_maps' AND timestamp != ''"
    ).fetchone()
    conn.close()
    return row[0] if row and row[0] else None


def _format_place_label(item: dict, fallback: str = "") -> tuple[str, str]:
    """
    Build a (display_label, location_string) tuple from an Apify item.
    Examples:
        ("Cubework — Buena Park, CA", "Buena Park, CA")
        ("ReadySpaces — Chula Vista, CA", "Chula Vista, CA")
        ("Saltbox Atlanta", "Atlanta, GA")  # fallback if title already contains city
    """
    brand = (item.get("title") or fallback or "").strip()
    city = (item.get("city") or "").strip()
    state = (item.get("state") or "").strip()

    location_parts = [p for p in (city, state) if p]
    location_str = ", ".join(location_parts)

    if brand and location_str:
        # Avoid double-naming if the brand title already includes the city
        if city.lower() in brand.lower():
            label = brand
        else:
            label = f"{brand} — {location_str}"
    else:
        label = brand or location_str or fallback

    return label, location_str


def review_to_signal(item: dict, place_name_fallback: str = "") -> Optional[Signal]:
    """
    Convert one Apify review item into a Signal.
    Returns None for items with no review text (rating-only ratings are noise).

    Note on Apify field semantics:
      - item["title"]   = the place/business title (e.g. "Cubework")
      - item["name"]    = the REVIEWER's name (NOT the place name)
      - item["city"], item["state"], item["address"] = the place location
    """
    review_id = item.get("reviewId") or item.get("reviewerId") or ""
    text = item.get("text") or item.get("textTranslated") or ""
    if not review_id or not text:
        return None

    place_label, location_str = _format_place_label(item, fallback=place_name_fallback)

    rating = item.get("stars")
    try:
        rating = float(rating) if rating is not None else None
    except (TypeError, ValueError):
        rating = None

    return Signal(
        id=f"gmaps_{review_id}",
        source="google_maps",
        source_url=item.get("reviewUrl") or item.get("url") or "",
        timestamp=item.get("publishedAtDate") or "",
        author=Author(
            name=item.get("name"),
            profile_url=item.get("reviewerUrl"),
            company=place_label,  # brand + city, used as the indexed grouping key
        ),
        content=Content(
            body=text,
            rating=rating,
        ),
        metadata={
            "place_name": place_label,
            "place_brand": item.get("title", ""),
            "place_id": item.get("placeId", ""),
            "place_url": item.get("url", ""),
            "place_total_reviews": item.get("reviewsCount"),
            "place_rating": item.get("totalScore"),
            "place_categories": item.get("categories") or [],
            "place_category_main": item.get("categoryName", ""),
            # Location — also set as a string under "location" so the
            # classifier prompt's {location} field maps to Saltbox markets.
            "location": location_str,
            "city": item.get("city", ""),
            "state": item.get("state", ""),
            "address": item.get("address", ""),
            "postal_code": item.get("postalCode", ""),
            "owner_response": item.get("responseFromOwnerText", ""),
            "owner_response_date": item.get("responseFromOwnerDate", ""),
            "review_likes": item.get("likesCount") or 0,
            "is_local_guide": item.get("isLocalGuide", False),
            "language": item.get("originalLanguage", ""),
            "reviewer_total_reviews": item.get("reviewerNumberOfReviews"),
            "photos": [
                p.get("url") if isinstance(p, dict) else p
                for p in (item.get("reviewImageUrls") or [])
            ],
        },
    )


def collect_google_maps_reviews(
    places: list[dict],
    max_reviews_per_place: int = 200,
    reviews_start_date: Optional[str] = None,
    dry_run: bool = True,
    max_cost_usd: float = 1.00,
    db_path=DB_PATH,
) -> list[Signal]:
    """
    Scrape Google Maps reviews for a set of places via Apify.

    places: list of {"name": str, "url": str} dicts
    max_reviews_per_place: hard cap per place (cost guard)
    reviews_start_date: ISO date — only fetch reviews newer than this
    dry_run: if True, prints estimate and returns [] without calling Apify
    max_cost_usd: refuses to run if estimate exceeds this ceiling
    """
    if not places:
        log.warning("No places provided.")
        return []

    estimate = estimate_cost(len(places), max_reviews_per_place)
    log.info(
        f"Cost estimate: ${estimate['estimated_usd']:.4f} "
        f"({estimate['places']} places × up to {max_reviews_per_place} reviews) "
        f"breakdown: run=${estimate['breakdown']['run']} "
        f"places=${estimate['breakdown']['places']} "
        f"reviews=${estimate['breakdown']['reviews']}"
    )

    if estimate["estimated_usd"] > max_cost_usd:
        log.error(
            f"Estimated cost ${estimate['estimated_usd']:.4f} exceeds "
            f"max_cost_usd ${max_cost_usd:.2f}. Aborting. "
            f"Pass --max-cost-usd to raise the ceiling."
        )
        return []

    if dry_run:
        log.info("DRY RUN — not calling Apify. Pass --live to actually scrape.")
        for p in places[:10]:
            log.info(f"  would scrape: {p['name']}")
        if len(places) > 10:
            log.info(f"  ... and {len(places) - 10} more")
        return []

    if not APIFY_API_TOKEN:
        log.error("APIFY_API_TOKEN not set. Add it to .env.")
        return []

    # Lazy import so dry-runs don't require apify-client to be installed
    from apify_client import ApifyClient

    client = ApifyClient(APIFY_API_TOKEN)

    run_input = {
        "startUrls": [{"url": p["url"]} for p in places],
        "maxReviews": max_reviews_per_place,
        "reviewsSort": "newest",
        "language": "en",
        "personalData": True,
        # Hard ceiling enforced by Apify itself, in case our estimate is wrong:
        "maxTotalChargeUsd": max_cost_usd,
    }
    if reviews_start_date:
        run_input["reviewsStartDate"] = reviews_start_date
        log.info(f"Delta scrape from {reviews_start_date}")

    log.info(
        f"Calling actor {APIFY_REVIEWS_ACTOR} for {len(places)} places "
        f"(max {max_reviews_per_place} reviews each)..."
    )
    run = client.actor(APIFY_REVIEWS_ACTOR).call(run_input=run_input)

    if not run or run.get("status") != "SUCCEEDED":
        log.error(f"Actor run failed or did not succeed: {run}")
        return []

    log.info(f"Run finished. Dataset: {run['defaultDatasetId']}")

    name_by_url = {p["url"]: p["name"] for p in places}

    new_signals: list[Signal] = []
    total_seen = 0
    skipped_no_text = 0
    for item in client.dataset(run["defaultDatasetId"]).iterate_items():
        total_seen += 1
        place_url = item.get("url") or item.get("placeUrl") or ""
        name_fallback = name_by_url.get(place_url, "")

        signal = review_to_signal(item, place_name_fallback=name_fallback)
        if signal is None:
            skipped_no_text += 1
            continue
        if insert_signal(signal, db_path):
            new_signals.append(signal)

    log.info(
        f"Apify returned {total_seen} items "
        f"(skipped {skipped_no_text} rating-only). "
        f"{len(new_signals)} new signals inserted."
    )
    return new_signals
