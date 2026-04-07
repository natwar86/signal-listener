#!/usr/bin/env python3
"""
Run data collectors.

Usage:
    python -m scripts.collect                          # Run all collectors
    python -m scripts.collect shopify                  # Shopify only
    python -m scripts.collect shopify --apps shipbob   # Single app
    python -m scripts.collect shopify --max-pages 3    # Test mode
"""

import sys
import argparse
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import init_db
from config import (
    DB_PATH,
    SHOPIFY_APPS,
    SHOPIFY_MIN_DELAY,
    SHOPIFY_MAX_DELAY,
    GOOGLE_MAPS_PLACES,
)
from collectors.base import PoliteFetcher
from collectors.shopify_reviews import collect_shopify_reviews
from collectors.google_maps import (
    collect_google_maps_reviews,
    get_latest_review_date,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("signal-listener")


def run_shopify(args):
    apps = args.apps if args.apps else SHOPIFY_APPS
    min_delay = args.min_delay or SHOPIFY_MIN_DELAY
    max_delay = args.max_delay or SHOPIFY_MAX_DELAY

    fetcher = PoliteFetcher(min_delay=min_delay, max_delay=max_delay)
    total_new = 0

    try:
        for app_slug in apps:
            signals = collect_shopify_reviews(
                app_slug=app_slug,
                fetcher=fetcher,
                max_pages=args.max_pages,
                save_html=args.save_html,
                skip_resolve=args.skip_resolve,
                db_path=DB_PATH,
            )
            total_new += len(signals)
    except KeyboardInterrupt:
        log.info("\nInterrupted — progress saved. Run again to resume.")
    finally:
        fetcher.close()

    log.info(f"Shopify collection complete. {total_new} new signals total.")


def run_google_maps(args):
    if not GOOGLE_MAPS_PLACES:
        log.error(
            "GOOGLE_MAPS_PLACES is empty in config.py. "
            "Add target places before running."
        )
        return

    places = GOOGLE_MAPS_PLACES
    if args.places:
        wanted = set(args.places)
        places = [p for p in places if p["name"] in wanted]
        if not places:
            log.error(f"No matches for --places {args.places}")
            return

    if args.limit:
        places = places[: args.limit]

    start_date = None
    if args.delta:
        start_date = get_latest_review_date()
        log.info(
            f"Delta mode: start_date={start_date or 'none (no prior data)'}"
        )

    signals = collect_google_maps_reviews(
        places=places,
        max_reviews_per_place=args.max_reviews_per_place,
        reviews_start_date=start_date,
        dry_run=not args.live,
        max_cost_usd=args.max_cost_usd,
        db_path=DB_PATH,
    )
    log.info(f"google_maps collection complete. {len(signals)} new signals.")


def main():
    parser = argparse.ArgumentParser(description="Run signal collectors")
    parser.add_argument("source", nargs="?", default="all",
                        choices=["all", "shopify", "reddit", "google_maps"],
                        help="Which collector to run")
    parser.add_argument("--apps", nargs="*", help="Specific app slugs (shopify only)")
    parser.add_argument("--max-pages", type=int, help="Max pages per app (testing)")
    parser.add_argument("--min-delay", type=float, help="Min delay between requests")
    parser.add_argument("--max-delay", type=float, help="Max delay between requests")
    parser.add_argument("--save-html", action="store_true", help="Save raw HTML for debugging")
    parser.add_argument("--skip-resolve", action="store_true", help="Skip store URL resolution")
    parser.add_argument("--verbose", action="store_true")

    # google_maps flags (cost-safety guards)
    parser.add_argument("--places", nargs="*",
                        help="Specific place names from GOOGLE_MAPS_PLACES (google_maps only)")
    parser.add_argument("--limit", type=int,
                        help="Limit number of places (google_maps only, testing)")
    parser.add_argument("--max-reviews-per-place", type=int, default=200,
                        help="Hard cap on reviews per place (google_maps, default 200)")
    parser.add_argument("--max-cost-usd", type=float, default=1.00,
                        help="Refuse to run if estimate exceeds this (google_maps, default $1.00)")
    parser.add_argument("--live", action="store_true",
                        help="Actually call Apify (google_maps). Default is dry-run.")
    parser.add_argument("--delta", action="store_true",
                        help="Use latest stored timestamp as start_date (google_maps)")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    init_db(DB_PATH)

    if args.source in ("all", "shopify"):
        run_shopify(args)

    if args.source in ("all", "reddit"):
        log.info("Reddit collector not yet implemented — skipping")

    if args.source == "google_maps":
        run_google_maps(args)


if __name__ == "__main__":
    main()
